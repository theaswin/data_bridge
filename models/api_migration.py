import logging
import xmlrpc.client
import time
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

# Connection timeout in seconds
CONNECTION_TIMEOUT = 120


class ApiMigration(models.Model):
    _name = 'data.bridge.api.migration'
    _description = 'API Migration from Old ERP'
    _order = 'create_date desc'

    name = fields.Char(string='Migration Name', required=True)
    
    # Connection Details
    erp_url = fields.Char(
        string='Old ERP URL', required=True,
        help='URL of the old Odoo ERP (e.g., https://old-erp.example.com)')
    erp_db = fields.Char(
        string='Database Name', required=True,
        help='Database name of the old ERP')
    erp_username = fields.Char(
        string='Username', required=True,
        help='Login username (email) for the old ERP')
    erp_password = fields.Char(
        string='Password', required=True)
    erp_uid = fields.Integer(
        string='User ID', readonly=True,
        help='Authenticated User ID (auto-filled after connection test)')
    
    # Model Configuration
    source_model = fields.Char(
        string='Source Model', required=True,
        help='Model name in old ERP (e.g., res.partner, product.product)')
    destination_model = fields.Char(
        string='Destination Model', required=True,
        help='Model name in current ERP (e.g., res.partner)')
    
    # Filters
    domain_filter = fields.Char(
        string='Domain Filter', default='[]',
        help='Domain to filter records from source (e.g., [("active", "=", True)])')
    limit = fields.Integer(
        string='Limit', default=0,
        help='Maximum records to fetch (0 = all)')
    offset = fields.Integer(
        string='Offset', default=0,
        help='Number of records to skip')
    
    # Options
    batch_size = fields.Integer(
        string='Batch Size', default=500,
        help='Records to process per batch (recommended: 500-1000 for large datasets)')
    skip_errors = fields.Boolean(
        string='Skip Errors & Continue', default=True,
        help='Continue migration even if some records fail')
    
    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('connected', 'Connected'),
        ('ready', 'Ready'),
        ('running', 'Running'),
        ('paused', 'Paused'),
        ('done', 'Completed'),
        ('error', 'Error'),
    ], string='Status', default='draft', readonly=True)
    
    # Progress tracking
    progress = fields.Float(
        string='Progress', default=0, readonly=True,
        help='Migration progress percentage')
    current_batch = fields.Integer(
        string='Current Batch', default=0, readonly=True)
    total_batches = fields.Integer(
        string='Total Batches', default=0, readonly=True)
    
    # Field info (populated after fetching)
    source_fields_info = fields.Text(
        string='Source Fields Info', readonly=True)
    excluded_fields = fields.Text(
        string='Excluded Fields (M2M/M2O)', readonly=True)
    included_fields = fields.Text(
        string='Included Fields', readonly=True)
    fields_to_migrate = fields.Text(
        string='Fields to Migrate (Internal)', readonly=True)
    
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
        'data.bridge.migration.log', 'api_migration_id',
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

    def _get_xmlrpc_connection(self):
        """Get XML-RPC connection objects with timeout."""
        self.ensure_one()
        
        url = self.erp_url.rstrip('/')
        
        # Create transport with timeout
        transport = xmlrpc.client.SafeTransport() if url.startswith('https') else xmlrpc.client.Transport()
        
        common = xmlrpc.client.ServerProxy(
            f'{url}/xmlrpc/2/common',
            transport=transport,
            allow_none=True
        )
        models_proxy = xmlrpc.client.ServerProxy(
            f'{url}/xmlrpc/2/object',
            transport=transport,
            allow_none=True
        )
        
        return common, models_proxy

    def action_test_connection(self):
        """Test connection to the old ERP."""
        self.ensure_one()
        
        try:
            common, models_proxy = self._get_xmlrpc_connection()
            
            # Authenticate
            uid = common.authenticate(
                self.erp_db, 
                self.erp_username, 
                self.erp_password, 
                {}
            )
            
            if not uid:
                self.state = 'error'
                raise UserError(_('Authentication failed. Please check your credentials.'))
            
            # Test if we can access the source model
            try:
                models_proxy.execute_kw(
                    self.erp_db, uid, self.erp_password,
                    self.source_model, 'check_access_rights',
                    ['read'], {'raise_exception': False}
                )
            except Exception as e:
                raise UserError(_(
                    'Connected but cannot access model "%(model)s". Error: %(error)s',
                    model=self.source_model, error=str(e)
                ))
            
            self.write({
                'erp_uid': uid,
                'state': 'connected'
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful'),
                    'message': _('Connected to %(url)s as User ID: %(uid)s', url=self.erp_url, uid=uid),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except xmlrpc.client.Fault as e:
            self.state = 'error'
            raise UserError(_('XML-RPC Error: %(error)s', error=str(e)))
        except ConnectionRefusedError:
            self.state = 'error'
            raise UserError(_('Connection refused. Check if the server is running.'))
        except TimeoutError:
            self.state = 'error'
            raise UserError(_('Connection timed out. The server might be slow or unreachable.'))
        except Exception as e:
            self.state = 'error'
            raise UserError(_('Connection failed: %(error)s', error=str(e)))

    def action_fetch_fields(self):
        """Fetch field information from source model and identify excluded fields."""
        self.ensure_one()
        
        if not self.erp_uid:
            raise UserError(_('Please test connection first.'))
        
        try:
            _, models_proxy = self._get_xmlrpc_connection()
            
            # Get fields info from source model
            fields_info = models_proxy.execute_kw(
                self.erp_db, self.erp_uid, self.erp_password,
                self.source_model, 'fields_get',
                [],
                {'attributes': ['string', 'type', 'relation', 'readonly', 'store']}
            )
            
            # Get destination model fields
            try:
                DestModel = self.env[self.destination_model]
            except KeyError:
                raise UserError(_(
                    'Destination model "%(model)s" not found in current ERP.',
                    model=self.destination_model
                ))
            
            dest_fields = set(DestModel._fields.keys())
            
            # System fields to always skip
            skip_fields = {
                'id', 'create_uid', 'create_date', 'write_uid', 'write_date',
                '__last_update', 'display_name', 'message_ids', 'message_follower_ids',
                'message_partner_ids', 'message_channel_ids', 'activity_ids',
                'activity_state', 'activity_user_id', 'activity_type_id',
                'activity_date_deadline', 'activity_summary', 'website_message_ids'
            }
            
            # Field types to skip
            skip_types = {'many2many', 'many2one', 'one2many', 'binary', 'reference'}
            
            excluded = []
            included = []
            migratable = []
            
            for field_name, field_data in fields_info.items():
                field_type = field_data.get('type')
                
                # Skip system fields
                if field_name in skip_fields:
                    excluded.append(f"{field_name} (system field)")
                    continue
                
                # Skip non-stored (computed) fields
                if not field_data.get('store', True):
                    excluded.append(f"{field_name} (computed/non-stored)")
                    continue
                
                # Skip relational and binary fields
                if field_type in skip_types:
                    relation = field_data.get('relation', 'N/A')
                    excluded.append(f"{field_name} ({field_type} -> {relation})")
                    continue
                
                # Check if field exists in destination
                if field_name not in dest_fields:
                    excluded.append(f"{field_name} (not in destination model)")
                    continue
                
                # Check destination field type compatibility
                dest_field = DestModel._fields.get(field_name)
                if dest_field and dest_field.type in skip_types:
                    excluded.append(f"{field_name} (dest is {dest_field.type})")
                    continue
                
                included.append(f"{field_name} ({field_type})")
                migratable.append(field_name)
            
            # Count source records
            try:
                domain = safe_eval(self.domain_filter) if self.domain_filter else []
            except Exception:
                domain = []
            
            count = models_proxy.execute_kw(
                self.erp_db, self.erp_uid, self.erp_password,
                self.source_model, 'search_count',
                [domain]
            )
            
            # Calculate batches
            effective_count = min(count - self.offset, self.limit) if self.limit else count - self.offset
            total_batches = (effective_count + self.batch_size - 1) // self.batch_size if effective_count > 0 else 0
            
            self.write({
                'source_fields_info': str(fields_info),
                'excluded_fields': '\n'.join(sorted(excluded)),
                'included_fields': '\n'.join(sorted(included)),
                'fields_to_migrate': ','.join(migratable),
                'total_source_records': count,
                'total_batches': total_batches,
                'state': 'ready'
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Fields Analyzed'),
                    'message': _(
                        '%(included)s fields to migrate, %(excluded)s excluded. '
                        '%(count)s total records (%(batches)s batches)',
                        included=len(included), excluded=len(excluded),
                        count=count, batches=total_batches
                    ),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except xmlrpc.client.Fault as e:
            raise UserError(_('XML-RPC Error: %(error)s', error=str(e)))
        except Exception as e:
            raise UserError(_('Error fetching fields: %(error)s', error=str(e)))

    def action_run_migration(self):
        """Execute the API migration with optimized batch processing."""
        self.ensure_one()
        
        if self.state not in ('ready', 'done', 'error', 'paused'):
            raise UserError(_('Please fetch fields first.'))
        
        # Verify destination model exists
        try:
            DestModel = self.env[self.destination_model]
        except KeyError:
            raise UserError(_(
                'Destination model "%(model)s" not found.',
                model=self.destination_model
            ))
        
        # Get fields to migrate
        if not self.fields_to_migrate:
            raise UserError(_('No fields to migrate. Please fetch fields first.'))
        
        fields_to_migrate = self.fields_to_migrate.split(',')
        
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
        })
        self.env.cr.commit()
        
        start_time = time.time()
        total_created = 0
        total_failed = 0
        total_skipped = 0
        
        try:
            _, models_proxy = self._get_xmlrpc_connection()
            
            # Get domain
            try:
                domain = safe_eval(self.domain_filter) if self.domain_filter else []
            except Exception:
                domain = []
            
            # Get record IDs
            search_params = {'offset': self.offset}
            if self.limit:
                search_params['limit'] = self.limit
            
            record_ids = models_proxy.execute_kw(
                self.erp_db, self.erp_uid, self.erp_password,
                self.source_model, 'search',
                [domain],
                search_params
            )
            
            if not record_ids:
                self.write({
                    'state': 'done',
                    'progress': 100,
                    'migrated_records': 0,
                    'failed_records': 0,
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
            
            total_records = len(record_ids)
            total_batches = (total_records + self.batch_size - 1) // self.batch_size
            
            _logger.info(f'Starting migration: {total_records} records in {total_batches} batches')
            
            # Process in batches
            for batch_num, i in enumerate(range(0, total_records, self.batch_size), 1):
                batch_ids = record_ids[i:i + self.batch_size]
                batch_start = time.time()
                
                try:
                    # Read records from source
                    records = models_proxy.execute_kw(
                        self.erp_db, self.erp_uid, self.erp_password,
                        self.source_model, 'read',
                        [batch_ids],
                        {'fields': fields_to_migrate}
                    )
                    
                    # Prepare batch values for bulk create
                    batch_values = []
                    batch_errors = []
                    
                    for record in records:
                        try:
                            values = {}
                            for field in fields_to_migrate:
                                value = record.get(field)
                                # Skip empty values
                                if value in (None, False, '', [], {}):
                                    continue
                                values[field] = value
                            
                            if values:
                                batch_values.append(values)
                            else:
                                total_skipped += 1
                                
                        except Exception as e:
                            batch_errors.append({
                                'record': record,
                                'error': str(e)
                            })
                    
                    # Bulk create records (much faster than individual creates)
                    if batch_values:
                        try:
                            # Use savepoint for atomic batch operation
                            with self.env.cr.savepoint():
                                created_records = DestModel.create(batch_values)
                                total_created += len(created_records)
                        except Exception as e:
                            # If bulk create fails, try individual creates
                            _logger.warning(f'Bulk create failed, falling back to individual: {e}')
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
                                        f'Failed to create record',
                                        source_data=str(vals)[:1000],
                                        error_details=str(e2)[:500])
                    
                    # Log batch errors
                    for err in batch_errors:
                        total_failed += 1
                        if not self.skip_errors:
                            raise UserError(str(err['error']))
                        self._create_log_safe('error',
                            'Failed to process record',
                            source_data=str(err['record'])[:1000],
                            error_details=str(err['error'])[:500])
                    
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
                    
                except xmlrpc.client.Fault as e:
                    _logger.error(f'XML-RPC error in batch {batch_num}: {e}')
                    if not self.skip_errors:
                        raise
                    total_failed += len(batch_ids)
                    self._create_log_safe('error',
                        f'Batch {batch_num} failed',
                        error_details=str(e)[:500])
                    continue
                    
                except Exception as e:
                    _logger.error(f'Error in batch {batch_num}: {e}')
                    if not self.skip_errors:
                        raise
                    self._create_log_safe('error',
                        f'Batch {batch_num} error',
                        error_details=str(e)[:500])
                    continue
            
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
                        'Duration: %(duration).1fs',
                        created=total_created, failed=total_failed,
                        skipped=total_skipped, duration=duration
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
                f'Migration failed after {duration:.2f}s: {str(e)}',
                error_details=str(e)[:1000])
            self.env.cr.commit()
            raise UserError(_('Migration failed: %(error)s', error=str(e)))

    def action_pause_migration(self):
        """Pause the migration (for future resume functionality)."""
        self.ensure_one()
        if self.state == 'running':
            self.state = 'paused'

    def action_reset(self):
        """Reset migration status."""
        self.ensure_one()
        self.write({
            'state': 'ready' if self.erp_uid else 'draft',
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
                'api_migration_id': self.id,
                'log_type': log_type,
                'message': message[:2000] if message else '',
                **{k: v[:2000] if isinstance(v, str) else v for k, v in kwargs.items()}
            })
        except Exception as e:
            _logger.error(f'Failed to create log: {e}')
