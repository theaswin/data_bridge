import base64
import logging
import time
import re
import json
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SqlMigration(models.Model):
    _name = 'data.bridge.sql.migration'
    _description = 'SQL File Migration'
    _order = 'create_date desc'

    name = fields.Char(string='Migration Name', required=True)
    
    # SQL File
    sql_file = fields.Binary(
        string='SQL File', required=True,
        help='Upload .sql database dump file')
    sql_filename = fields.Char(string='Filename')
    
    # Table Configuration
    source_table = fields.Char(
        string='Source Table Name', required=True,
        help='Table name in the SQL file')
    destination_model = fields.Char(
        string='Destination Model', required=True,
        help='Odoo model name (e.g., res.partner)')
    
    # Options
    batch_size = fields.Integer(
        string='Batch Size', default=1000,
        help='Records to process per batch (recommended: 1000 for large datasets)')
    truncate_destination = fields.Boolean(
        string='Clear Destination First', default=False,
        help='Delete all existing records in destination model before migration')
    skip_errors = fields.Boolean(
        string='Skip Errors & Continue', default=True,
        help='Continue migration even if some records fail')
    
    # Column Mapping (auto-detected)
    detected_columns = fields.Text(
        string='Detected Columns', readonly=True)
    excluded_columns = fields.Text(
        string='Excluded Columns', readonly=True,
        help='Columns that will be skipped (M2M, M2O, system fields)')
    included_columns = fields.Text(
        string='Included Columns', readonly=True)
    column_mapping_json = fields.Text(
        string='Column Mapping (Internal)', readonly=True)
    
    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('parsed', 'File Parsed'),
        ('ready', 'Ready'),
        ('running', 'Running'),
        ('done', 'Completed'),
        ('error', 'Error'),
    ], string='Status', default='draft', readonly=True)
    
    # Progress tracking
    progress = fields.Float(
        string='Progress', default=0, readonly=True)
    current_batch = fields.Integer(
        string='Current Batch', default=0, readonly=True)
    total_batches = fields.Integer(
        string='Total Batches', default=0, readonly=True)
    
    # Statistics
    total_source_records = fields.Integer(
        string='Total Source Records', readonly=True)
    migrated_records = fields.Integer(
        string='Migrated Records', readonly=True)
    failed_records = fields.Integer(
        string='Failed Records', readonly=True)
    skipped_records = fields.Integer(
        string='Skipped Records', readonly=True)
    last_migration_date = fields.Datetime(
        string='Last Migration Date', readonly=True)
    migration_duration = fields.Float(
        string='Duration (seconds)', readonly=True)
    records_per_second = fields.Float(
        string='Records/Second', readonly=True, digits=(10, 2))
    
    migration_log_ids = fields.One2many(
        'data.bridge.migration.log', 'sql_migration_id',
        string='Migration Logs')
    log_count = fields.Integer(
        string='Log Count', compute='_compute_log_count')
    error_count = fields.Integer(
        string='Error Count', compute='_compute_log_count')
    
    notes = fields.Text(string='Notes')

    @api.depends('migration_log_ids')
    def _compute_log_count(self):
        for record in self:
            record.log_count = len(record.migration_log_ids)
            record.error_count = len(record.migration_log_ids.filtered(
                lambda l: l.log_type == 'error'))

    def _parse_sql_file_streaming(self):
        """Parse SQL file using streaming to handle large files efficiently."""
        self.ensure_one()
        
        if not self.sql_file:
            raise UserError(_('Please upload a SQL file first.'))
        
        # Decode the file
        try:
            sql_content = base64.b64decode(self.sql_file).decode('utf-8', errors='replace')
        except Exception as e:
            raise UserError(_('Failed to read SQL file: %(error)s', error=str(e)))
        
        table_name = self.source_table.strip()
        table_pattern = re.escape(table_name)
        
        # More robust pattern for INSERT statements
        # Handles: INSERT INTO table (...) VALUES (...), (...);
        # And: INSERT INTO `table` (...) VALUES (...);
        insert_pattern = rf"INSERT\s+(?:INTO\s+)?[`\"\[]?{table_pattern}[`\"\]]?\s*\(([^)]+)\)\s*VALUES\s*"
        
        # Find all INSERT statements
        all_columns = []
        all_values = []
        
        # Split by INSERT statements
        parts = re.split(insert_pattern, sql_content, flags=re.IGNORECASE)
        
        if len(parts) < 2:
            # Try alternate pattern without INTO
            insert_pattern2 = rf"INSERT\s+[`\"\[]?{table_pattern}[`\"\]]?\s*\(([^)]+)\)\s*VALUES\s*"
            parts = re.split(insert_pattern2, sql_content, flags=re.IGNORECASE)
        
        if len(parts) < 2:
            raise UserError(_(
                'No INSERT statements found for table "%(table)s" in the SQL file. '
                'Make sure the table name matches exactly.',
                table=table_name
            ))
        
        # Process each INSERT statement
        for i in range(1, len(parts), 2):
            if i >= len(parts):
                break
                
            col_str = parts[i]
            
            # Parse columns from first match
            if not all_columns:
                all_columns = [c.strip().strip('`"[]') for c in col_str.split(',')]
            
            # Get the values part (everything until next INSERT or end)
            if i + 1 < len(parts):
                values_str = parts[i + 1]
                # Find where values end (at semicolon or next INSERT)
                end_match = re.search(r';(?:\s*(?:INSERT|$))?', values_str, re.IGNORECASE)
                if end_match:
                    values_str = values_str[:end_match.start() + 1]
                
                # Parse values
                parsed_values = self._parse_values_optimized(values_str)
                all_values.extend(parsed_values)
        
        return all_columns, all_values

    def _parse_values_optimized(self, values_str):
        """Optimized value parsing for large datasets."""
        values_str = values_str.strip().rstrip(';').strip()
        if not values_str:
            return []
        
        value_sets = []
        current_values = []
        current_value = ''
        in_string = False
        string_char = None
        paren_depth = 0
        escape_next = False
        
        for char in values_str:
            if escape_next:
                current_value += char
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                current_value += char
                continue
            
            if not in_string:
                if char in ('"', "'"):
                    in_string = True
                    string_char = char
                    current_value += char
                elif char == '(':
                    paren_depth += 1
                    if paren_depth == 1:
                        current_value = ''
                    else:
                        current_value += char
                elif char == ')':
                    paren_depth -= 1
                    if paren_depth == 0:
                        if current_value.strip():
                            current_values.append(self._clean_value(current_value))
                        if current_values:
                            value_sets.append(current_values)
                        current_values = []
                        current_value = ''
                    else:
                        current_value += char
                elif char == ',' and paren_depth == 1:
                    current_values.append(self._clean_value(current_value))
                    current_value = ''
                elif char == ',' and paren_depth == 0:
                    pass  # Separator between value sets
                else:
                    current_value += char
            else:
                current_value += char
                if char == string_char:
                    in_string = False
                    string_char = None
        
        return value_sets

    def _clean_value(self, value):
        """Clean and convert a SQL value."""
        value = value.strip()
        
        if not value:
            return None
        
        upper_val = value.upper()
        if upper_val == 'NULL':
            return None
        if upper_val == 'TRUE':
            return True
        if upper_val == 'FALSE':
            return False
        if upper_val in ('NOW()', 'CURRENT_TIMESTAMP', 'CURRENT_DATE'):
            return fields.Datetime.now()
        
        # Remove quotes
        if len(value) >= 2:
            if (value[0] == "'" and value[-1] == "'") or \
               (value[0] == '"' and value[-1] == '"'):
                value = value[1:-1]
                # Unescape
                value = value.replace("\\'", "'")
                value = value.replace('\\"', '"')
                value = value.replace('\\\\', '\\')
                value = value.replace('\\n', '\n')
                value = value.replace('\\r', '\r')
                value = value.replace('\\t', '\t')
                return value if value else None
        
        # Try numeric conversion
        try:
            if '.' in value:
                return float(value)
            return int(value)
        except (ValueError, TypeError):
            return value

    def action_parse_file(self):
        """Parse the SQL file and detect columns."""
        self.ensure_one()
        
        try:
            columns, values = self._parse_sql_file_streaming()
            
            # Get destination model fields info
            try:
                DestModel = self.env[self.destination_model]
            except KeyError:
                raise UserError(_(
                    'Destination model "%(model)s" not found.',
                    model=self.destination_model
                ))
            
            # System fields to skip
            skip_fields = {
                'id', 'create_uid', 'create_date', 'write_uid', 'write_date',
                '__last_update', 'display_name', 'message_ids', 'message_follower_ids',
                'message_partner_ids', 'activity_ids'
            }
            
            # Field types to skip
            skip_types = {'many2many', 'many2one', 'one2many', 'binary', 'reference'}
            
            excluded = []
            included = []
            field_mapping = {}  # {source_index: dest_field_name}
            
            for idx, col in enumerate(columns):
                col_lower = col.lower()
                
                if col_lower in skip_fields:
                    excluded.append(f"{col} (system field)")
                    continue
                
                if col_lower in DestModel._fields:
                    field = DestModel._fields[col_lower]
                    if field.type in skip_types:
                        excluded.append(f"{col} ({field.type})")
                    elif not field.store:
                        excluded.append(f"{col} (computed/non-stored)")
                    else:
                        included.append(f"{col} ({field.type})")
                        field_mapping[idx] = col_lower
                else:
                    excluded.append(f"{col} (not in destination)")
            
            if not field_mapping:
                raise UserError(_('No mappable fields found between source and destination.'))
            
            # Calculate batches
            total_batches = (len(values) + self.batch_size - 1) // self.batch_size if values else 0
            
            self.write({
                'detected_columns': '\n'.join(columns),
                'excluded_columns': '\n'.join(excluded),
                'included_columns': '\n'.join(included),
                'column_mapping_json': json.dumps(field_mapping),
                'total_source_records': len(values),
                'total_batches': total_batches,
                'state': 'ready'
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('File Parsed Successfully'),
                    'message': _(
                        '%(cols)s columns detected, %(included)s will be migrated. '
                        '%(records)s records found (%(batches)s batches)',
                        cols=len(columns), included=len(included),
                        records=len(values), batches=total_batches
                    ),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except UserError:
            raise
        except Exception as e:
            self.state = 'error'
            _logger.exception('Error parsing SQL file')
            raise UserError(_('Error parsing SQL file: %(error)s', error=str(e)))

    def action_run_migration(self):
        """Execute the SQL file migration with ORM (safest method)."""
        self.ensure_one()
        
        if self.state not in ('ready', 'done', 'error'):
            raise UserError(_('Please parse the file first.'))
        
        try:
            DestModel = self.env[self.destination_model]
        except KeyError:
            raise UserError(_(
                'Destination model "%(model)s" not found.',
                model=self.destination_model
            ))
        
        if not self.column_mapping_json:
            raise UserError(_('Column mapping not found. Please parse the file again.'))
        
        try:
            field_mapping = json.loads(self.column_mapping_json)
            # Convert string keys back to integers
            field_mapping = {int(k): v for k, v in field_mapping.items()}
        except Exception:
            raise UserError(_('Invalid column mapping. Please parse the file again.'))
        
        # Disable tracking for performance
        DestModel = DestModel.with_context(
            tracking_disable=True,
            mail_notrack=True,
            mail_create_nolog=True,
            no_reset_password=True,
            import_file=True,
        )
        
        self.write({
            'state': 'running',
            'progress': 0,
            'current_batch': 0,
            'migrated_records': 0,
            'failed_records': 0,
            'skipped_records': 0,
        })
        self.env.cr.commit()
        
        start_time = time.time()
        total_created = 0
        total_failed = 0
        total_skipped = 0
        
        try:
            # Re-parse the file (we don't store all values in DB to save space)
            columns, all_values = self._parse_sql_file_streaming()
            
            if not all_values:
                self.write({
                    'state': 'done',
                    'progress': 100,
                    'last_migration_date': fields.Datetime.now()
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('No Records'),
                        'message': _('No records found to migrate.'),
                        'type': 'warning',
                        'sticky': False,
                    }
                }
            
            # Truncate destination if requested
            if self.truncate_destination:
                try:
                    _logger.info(f'Truncating destination model {self.destination_model}')
                    DestModel.search([]).unlink()
                    self.env.cr.commit()
                except Exception as e:
                    _logger.warning(f'Could not truncate: {e}')
            
            total_records = len(all_values)
            total_batches = (total_records + self.batch_size - 1) // self.batch_size
            
            _logger.info(f'Starting SQL migration: {total_records} records in {total_batches} batches')
            
            # Process in batches using ORM (safer than raw SQL)
            for batch_num, i in enumerate(range(0, total_records, self.batch_size), 1):
                batch = all_values[i:i + self.batch_size]
                batch_start = time.time()
                
                # Prepare batch values for bulk create
                batch_values = []
                
                for row in batch:
                    try:
                        values = {}
                        for src_idx, dest_field in field_mapping.items():
                            if src_idx < len(row):
                                value = row[src_idx]
                                if value is not None:
                                    values[dest_field] = value
                        
                        if values:
                            batch_values.append(values)
                        else:
                            total_skipped += 1
                            
                    except Exception as e:
                        total_failed += 1
                        if not self.skip_errors:
                            raise
                        _logger.warning(f'Error preparing record: {e}')
                
                # Bulk create with savepoint
                if batch_values:
                    try:
                        with self.env.cr.savepoint():
                            created = DestModel.create(batch_values)
                            total_created += len(created)
                    except Exception as e:
                        _logger.warning(f'Bulk create failed in batch {batch_num}, trying individual: {e}')
                        # Fallback to individual creates
                        for vals in batch_values:
                            try:
                                with self.env.cr.savepoint():
                                    DestModel.create(vals)
                                    total_created += 1
                            except Exception as e2:
                                total_failed += 1
                                if not self.skip_errors:
                                    raise
                                self._create_log_safe('error',
                                    'Failed to create record',
                                    source_data=str(vals)[:1000],
                                    error_details=str(e2)[:500])
                
                # Update progress
                progress = (batch_num / total_batches) * 100
                batch_duration = time.time() - batch_start
                
                self.write({
                    'progress': progress,
                    'current_batch': batch_num,
                    'migrated_records': total_created,
                    'failed_records': total_failed,
                    'skipped_records': total_skipped,
                })
                self.env.cr.commit()
                
                _logger.info(
                    f'Batch {batch_num}/{total_batches} completed in {batch_duration:.2f}s '
                    f'({len(batch_values)} records)'
                )
            
            # Calculate statistics
            duration = time.time() - start_time
            records_per_second = total_created / duration if duration > 0 else 0
            
            self.write({
                'state': 'done',
                'progress': 100,
                'migrated_records': total_created,
                'failed_records': total_failed,
                'skipped_records': total_skipped,
                'last_migration_date': fields.Datetime.now(),
                'migration_duration': duration,
                'records_per_second': records_per_second,
            })
            
            self._create_log_safe('success',
                f'Migration completed: {total_created} created, {total_failed} failed, {total_skipped} skipped. '
                f'Duration: {duration:.2f}s ({records_per_second:.1f} rec/s)',
                is_batch_summary=True,
                records_created=total_created,
                records_failed=total_failed)
            
            self.env.cr.commit()
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Migration Complete'),
                    'message': _(
                        'Created: %(created)s, Failed: %(failed)s, Skipped: %(skipped)s. '
                        'Duration: %(duration).1fs (%(speed).1f rec/s)',
                        created=total_created, failed=total_failed,
                        skipped=total_skipped, duration=duration,
                        speed=records_per_second
                    ),
                    'type': 'success' if total_failed == 0 else 'warning',
                    'sticky': True,
                }
            }
            
        except Exception as e:
            duration = time.time() - start_time
            self.write({
                'state': 'error',
                'migration_duration': duration,
                'migrated_records': total_created,
                'failed_records': total_failed,
            })
            self._create_log_safe('error',
                f'Migration failed after {duration:.2f}s',
                error_details=str(e)[:1000])
            self.env.cr.commit()
            _logger.exception('Migration failed')
            raise UserError(_('Migration failed: %(error)s', error=str(e)))

    def action_reset(self):
        """Reset migration status."""
        self.ensure_one()
        self.write({
            'state': 'ready' if self.column_mapping_json else 'draft',
            'progress': 0,
            'current_batch': 0,
            'migrated_records': 0,
            'failed_records': 0,
            'skipped_records': 0,
            'migration_duration': 0,
            'records_per_second': 0,
        })

    def action_clear_logs(self):
        """Clear all migration logs."""
        self.ensure_one()
        self.migration_log_ids.unlink()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Logs Cleared'),
                'message': _('All migration logs have been cleared.'),
                'type': 'success',
                'sticky': False,
            }
        }

    def _create_log_safe(self, log_type, message, **kwargs):
        """Create a migration log entry with error handling."""
        try:
            self.env['data.bridge.migration.log'].create({
                'sql_migration_id': self.id,
                'log_type': log_type,
                'message': message[:2000] if message else '',
                **{k: v[:2000] if isinstance(v, str) else v for k, v in kwargs.items()}
            })
        except Exception as e:
            _logger.error(f'Failed to create log: {e}')
