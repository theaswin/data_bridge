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
    migration_date = fields.Datetime(
        string='Migration Date', readonly=True, copy=False) 
    log_type = fields.Selection([
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ], string='Log Type', readonly=True, copy=False)
    message = fields.Text(string='Message', readonly=True, copy=False)
    is_batch_summary = fields.Text(string="Is Batch Summary", default=False, copy=False)
    
    # Model Configuration
    source_model = fields.Char(
        string='Source Model', required=True,
        help='Model name in old ERP (e.g., res.partner, product.product)')
    destination_model = fields.Char(
        string='Destination Model',
        help='Model name in current ERP (e.g., res.partner)')
    
    # Filters
    domain_filter = fields.Char(
        string='Domain Filter', default='[]', copy=False,
        help='Domain to filter records from source (e.g., [("active", "=", True)])')
    limit = fields.Integer(
        string='Limit', default=0, copy=False,
        help='Maximum records to fetch (0 = all)')
    offset = fields.Integer(
        string='Offset', default=0, copy=False,
        help='Number of records to skip')
    
    # Options
    batch_size = fields.Integer(
        string='Batch Size', default=500, copy=False,
        help='Records to process per batch (recommended: 500-1000 for large datasets)')
    skip_errors = fields.Boolean(
        string='Skip Errors & Continue', default=True, copy=False,
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
    ], string='Status', default='draft', readonly=True, copy=False)
    
    # Progress tracking
    progress = fields.Float(
        string='Progress', compute='_compute_progress', store=False, copy=False,
        digits=(16, 2), help='Migration progress percentage')
    current_batch = fields.Integer(
        string='Current Batch', default=0, readonly=True, copy=False)
    total_batches = fields.Integer(
        string='Total Batches', compute='_compute_total_batches', store=True, copy=False)
    
    # Field info (populated after fetching)
    source_fields_info = fields.Text(
        string='Source Fields Info', readonly=True, copy=False)
    excluded_fields = fields.Text(
        string='Excluded Fields (M2M/M2O)', readonly=True, copy=False)
    included_fields = fields.Text(
        string='Included Fields', readonly=True, copy=False)
    fields_to_migrate = fields.Text(
        string='Fields to Migrate (Internal)', readonly=True, copy=False)
    
    # Statistics
    total_source_records = fields.Integer(
        string='Total Source Records', readonly=True, copy=False)
    migrated_records = fields.Integer(
        string='Migrated Records', readonly=True, copy=False)
    failed_records = fields.Integer(
        string='Failed Records', readonly=True, copy=False)
    skipped_records = fields.Integer(
        string='Skipped Records', readonly=True, copy=False)
    last_migration_date = fields.Datetime(
        string='Last Migration Date', readonly=True, copy=False)
    migration_duration = fields.Float(
        string='Duration (seconds)', compute='_compute_migration_duration', store=False, copy=False)
    records_per_second = fields.Float(
        string='Records/Second', readonly=True, digits=(10, 2), copy=False)
    
    log_count = fields.Integer(
        string='Log Count', compute='_compute_log_count', copy=False)
    error_count = fields.Integer(
        string='Error Count', compute='_compute_log_count', copy=False)
    
    migrator_ids = fields.One2many(
        'migrator.migrator', 'migration_id', string='Migrators',
        readonly=False, copy=False)
    
    notes = fields.Text(string='Notes', copy=False)
    all_source_fields = fields.Text(
        string='All Fields in Old ERP', readonly=True, copy=False,
        help='List of all fields available in the source model of the old ERP')
    field_mapping_ids = fields.One2many(
        'data.bridge.field.mapping', 'migration_id', string='Field Mappings',
        copy=False, help='Mapping of source fields to destination fields')
    batch = fields.Integer(string="Batch", default=0, copy=False)
    duration = fields.Float(string="Duration", default=0, copy=False)
    destination_model_id = fields.Many2one(
        'ir.model', string='Destination Model', copy=False)

        

    @api.depends('migrator_ids.duration')
    def _compute_migration_duration(self):
        for record in self:
            record.migration_duration = sum(record.migrator_ids.mapped('duration'))
            
    @api.depends('migrated_records', 'failed_records', 'skipped_records', 'total_source_records')
    def _compute_progress(self):
        for record in self:
            if record.total_source_records > 0:
                completed = record.total_source_records - record.failed_records
                print("Completed", completed)
                record.progress = (completed / record.total_source_records) * 100.0
                print("Total =================",  (completed / record.total_source_records) * 100.0)
            else:
                record.progress = 0.0

    @api.depends('total_source_records', 'limit', 'batch_size')
    def _compute_total_batches(self):
        for record in self:
            # Use limit if specified, otherwise use batch_size for calculations
            divisor = record.limit if record.limit > 0 else record.batch_size
            if divisor > 0:
                record.total_batches = int((record.total_source_records + divisor - 1) / divisor)
            else:
                record.total_batches = 0

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
    
    def get_models_data(self):
        self.ensure_one()
        url = self.erp_url
        db = self.erp_db
        username = self.erp_username
        password = self.erp_password

        try:
            print(f"Connecting to {url}...")
            common, models_proxy = self._get_xmlrpc_connection()
            uid = self.erp_uid or common.authenticate(db, username, password, {})

            if not uid:
                raise UserError(_("Authentication failed."))

            print(f"Logged in. UID: {uid}")

            print(f"Fetching count for {self.source_model}...")
            domain = safe_eval(self.domain_filter or '[]')
            record_count = models_proxy.execute_kw(
                db, uid, password,
                self.source_model, 'search_count',
                [domain]
            )
            print(f"Total records found: {record_count}")

            self.write({
                'erp_uid': uid,
                'total_source_records': record_count,
            })
            
            # Run field analysis automatically
            self.action_analyze_fields()

        except Exception as e:
            _logger.error("Failed to fetch model data: %s", str(e))
            raise UserError(_("Failed to fetch model data: %s") % str(e))

    def action_analyze_fields(self):
        """Analyze fields in source and destination models to identify what can be migrated."""
        self.ensure_one()
        if not self.erp_uid:
            raise UserError(_("Please test connection first."))
            
        try:
            common, models_proxy = self._get_xmlrpc_connection()
            
            # 1. Get Source Fields Metadata
            source_fields_all = models_proxy.execute_kw(
                self.erp_db, self.erp_uid, self.erp_password,
                self.source_model, 'fields_get',
                [], {'attributes': ['type', 'store', 'string', 'required']}
            )
            
            # 2. Get Destination Model
            dest_model_name = self.destination_model_id.model or self.destination_model
            if not dest_model_name:
                raise UserError(_("Please specify a destination model."))
            
            if dest_model_name not in self.env:
                raise UserError(_("Destination model %s not found in current ERP.") % dest_model_name)
                
            DestModel = self.env[dest_model_name]
            dest_fields = DestModel._fields
            
            # 3. Identify Magic/System Fields to Always Exclude
            magic_fields = {
                'id', 'create_uid', 'write_uid', 'create_date', 'write_date', 
                'display_name', '__last_update', 'xmlid'
            }
            
            included = []
            excluded = []
            all_fields_info = []
            
            for f_name, info in sorted(source_fields_all.items()):
                f_type = info.get('type')
                is_stored = info.get('store', True)
                f_label = info.get('string', f_name)
                
                info_str = f"{f_name} ({f_label}) [{f_type}]"
                all_fields_info.append(info_str)
                
                # Exclusion criteria:
                # - Is a magic field
                # - Doesn't exist in destination
                # - Is a relational field (M2O, O2M, M2M) - current limitation
                # - Is not stored in source (computed/function field)
                # - Is not writable in destination
                
                reason = ""
                if f_name in magic_fields:
                    reason = "System field"
                elif f_name not in dest_fields:
                    reason = "Missing in destination"
                elif f_type in ('many2one', 'one2many', 'many2many', 'reference'):
                    reason = f"Relational field ({f_type})"
                elif not is_stored:
                    reason = "Not stored in source"
                elif not dest_fields[f_name].store or dest_fields[f_name].compute:
                    # In Odoo 17+, we should check if it's computed and NOT stored
                    if dest_fields[f_name].compute and not dest_fields[f_name].store:
                        reason = "Computed/Read-only in destination"
                
                if reason:
                    excluded.append(f"{info_str} - {reason}")
                else:
                    included.append(info_str)
            
            # Clear existing field mappings
            self.field_mapping_ids.unlink()
            
            # Create field mapping records for all source fields
            field_mapping_vals = []
            for f_name in sorted(source_fields_all.keys()):
                f_type = source_fields_all[f_name].get('type', '')
                field_mapping_vals.append((0, 0, {
                    'field_name': f_name,
                    'field_type': f_type,
                    'restore_to_id': False,
                }))
            
            # Sync the models if they were different
            self.write({
                'all_source_fields': "\n".join(all_fields_info),
                'included_fields': "\n".join(included),
                'excluded_fields': "\n".join(excluded),
                'fields_to_migrate': ",".join([f.split(' ')[0] for f in included]),
                'destination_model': dest_model_name if not self.destination_model else self.destination_model,
                'field_mapping_ids': field_mapping_vals,
            })
            
        except Exception as e:
            raise UserError(_("Field Analysis Error: %s") % str(e))

    def load_steps(self):
        for rec in self:

            # Delete only this migration's batches
            self.env['migrator.migrator'].search([
                ('migration_id', '=', rec.id)
            ]).unlink()

            print(f"Generating {rec.total_batches} batch lines...")
            for i in range(rec.total_batches):

                offset = i * rec.batch_size
                limit = rec.batch_size

                start = offset + 1
                end = offset + limit

                print(f"Creating Batch {i+1}: Offset {offset}, Limit {limit}, Range {start}-{end}")
                self.env['migrator.migrator'].create({
                    'migration_id': rec.id,
                    'batch': i + 1,
                    'offset': offset,
                    'limit': limit,
                    'data_range': f'{start} - {end}',
            })
            print("Batch generation completed.")



    def action_ready(self):
        for rec in self:
            self.state = 'ready'

            # Delete only this migration's batches
            self.env['migrator.migrator'].search([
                ('migration_id', '=', rec.id)
            ]).unlink()

            print(f"Generating {rec.total_batches} batch lines...")
            for i in range(rec.total_batches):

                offset = i * rec.batch_size
                limit = rec.batch_size

                start = offset + 1
                end = offset + limit

                print(f"Creating Batch {i+1}: Offset {offset}, Limit {limit}, Range {start}-{end}")
                self.env['migrator.migrator'].create({
                    'migration_id': rec.id,
                    'batch': i + 1,
                    'offset': offset,
                    'limit': limit,
                    'data_range': f'{start} - {end}',
            })
            print("Batch generation completed.")


class MigratorMigrator(models.Model):
    _name = 'migrator.migrator'
    _description = 'Migrator Batch'

    migration_id = fields.Many2one('data.bridge.api.migration', string='Migration', ondelete='cascade', copy=False)
    batch = fields.Integer(string='Batch', copy=False)
    limit = fields.Integer(string='Limit', copy=False)
    offset = fields.Integer(string='Offset', copy=False)
    data_range = fields.Char(string='Data Range', copy=False)
    duration = fields.Float(string='Duration (seconds)', copy=False)
    progress = fields.Float(string='Progress (%)', digits=(16, 2), copy=False)
    is_done = fields.Boolean(string='Done', default=False, copy=False)

    def action_continue(self):
        """Execute the migration for this specific batch."""
        self.ensure_one()
        migration = self.migration_id
        if not migration:
            return
            
        print(f"=== Starting Migration Batch {self.batch} ===")
        print(f"Offset: {self.offset}, Limit: {self.limit}, Range: {self.data_range}")
        
        start_time = time.time()
        try:
            # Get field mappings (only mapped fields will be migrated)
            field_mappings = migration.field_mapping_ids.filtered(lambda m: m.restore_to_id)
            
            if not field_mappings:
                raise UserError(_("No field mappings configured. Please map at least one field in the Field Mapping tab."))
            
            # Build mapping with type info: source_field_name -> {dest_field, src_type, dest_type, dest_relation}
            field_map = {}
            for m in field_mappings:
                field_map[m.field_name] = {
                    'dest_field': m.restore_to_id.name,
                    'src_type': m.field_type,
                    'dest_type': m.restore_to_id.ttype,
                    'dest_relation': m.restore_to_id.relation,  # Related model for Many2one
                }
            source_fields = list(field_map.keys())
            
            print(f"Field mappings configured: {len(field_map)}")
            for src, info in field_map.items():
                print(f"  {src} ({info['src_type']}) -> {info['dest_field']} ({info['dest_type']})")
            
            print("Connecting to source ERP...")
            common, models_proxy = migration._get_xmlrpc_connection()
            uid = migration.erp_uid
            
            # 1. Fetch Records from Old ERP
            print(f"Searching for records in {migration.source_model}...")
            domain = safe_eval(migration.domain_filter or '[]')
            ids = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                migration.source_model, 'search',
                [domain],
                {'offset': self.offset, 'limit': self.limit}
            )
            
            if not ids:
                print("No records found in this range.")
                self.is_done = True
                return

            # 2. Get Local Model
            dest_model_name = migration.destination_model_id.model or migration.destination_model
            if not dest_model_name:
                raise UserError(_("Destination model not defined."))
            
            DestModel = self.env[dest_model_name]
            
            print(f"Fetching {len(ids)} records with fields: {source_fields}")
            
            records = []
            try:
                print(f"Attempting batch read of {len(ids)} records...")
                records = models_proxy.execute_kw(
                    migration.erp_db, uid, migration.erp_password,
                    migration.source_model, 'read',
                    [ids],
                    {'fields': source_fields}
                )
            except Exception as read_err:
                print(f"Batch read failed: {read_err}. Falling back to one-by-one reading...")
                for i, rid in enumerate(ids):
                    print(f"Reading record {i+1}/{len(ids)} (ID: {rid})...", end="\r")
                    try:
                        record_data = models_proxy.execute_kw(
                            migration.erp_db, uid, migration.erp_password,
                            migration.source_model, 'read',
                            [[rid]],
                            {'fields': source_fields}
                        )
                        if record_data:
                            records.append(record_data[0])
                    except Exception as single_err:
                        print(f"\nFailed to read record {rid}: {single_err}")
                        error_msg = f"Failed to read record from Old ERP: ID {rid} - Error: {single_err}"
                        migration.notes = (migration.notes or "") + f"\n{error_msg}"
                        migration.failed_records += 1
                print("\nOne-by-one reading completed.")

            if not records:
                print("No record data could be retrieved.")
                return
                
            print(f"Fetched {len(records)} records. Creating locally...")
            
            # 3. Create records in destination model
            count = 0
            DestModel = DestModel.with_context(
                tracking_disable=True,
                mail_notrack=True,
                mail_create_nolog=True,
                import_file=True,
            )
            
            # Cache for Many2one lookups to avoid repeated searches
            m2o_cache = {}
            
            # Get destination model fields info for relation lookup
            dest_fields_info = DestModel._fields
            
            for i, vals in enumerate(records):
                print(f"Processing record {i+1}/{len(records)} (Remote ID: {vals.get('id')})...", end="\r")
                
                # Map source fields to destination fields
                new_vals = {}
                for src_field, info in field_map.items():
                    if src_field not in vals or vals[src_field] is False:
                        continue
                    
                    src_value = vals[src_field]
                    dest_field = info['dest_field']
                    src_type = info['src_type']
                    dest_type = info['dest_type']
                    
                    # Handle Many2one fields
                    if src_type == 'many2one' and dest_type == 'many2one':
                        # Many2one value from old ERP is [id, name] or just id
                        m2o_name = None
                        if isinstance(src_value, (list, tuple)) and len(src_value) >= 2:
                            m2o_name = src_value[1]
                        elif isinstance(src_value, str):
                            m2o_name = src_value
                        else:
                            print(f"\n  Skipping {src_field}: unexpected value format {type(src_value)}")
                            continue
                        
                        if not m2o_name:
                            continue
                        
                        # Get the related model from destination field definition
                        if dest_field not in dest_fields_info:
                            print(f"\n  Skipping {src_field}: dest field '{dest_field}' not found in model")
                            continue
                        
                        dest_field_obj = dest_fields_info[dest_field]
                        related_model = getattr(dest_field_obj, 'comodel_name', None)
                        
                        if not related_model or related_model not in self.env:
                            print(f"\n  Skipping {src_field}: related model '{related_model}' not found")
                            continue
                        
                        # Use cache to avoid repeated searches
                        cache_key = (related_model, m2o_name)
                        if cache_key in m2o_cache:
                            matched_id = m2o_cache[cache_key]
                        else:
                            # Search for matching record by name in related model
                            RelatedModel = self.env[related_model]
                            matched_record = RelatedModel.search([('name', '=', m2o_name)], limit=1)
                            matched_id = matched_record.id if matched_record else False
                            m2o_cache[cache_key] = matched_id
                            
                            if matched_id:
                                print(f"\n  Matched {src_field} -> {dest_field}: '{m2o_name}' found in {related_model} (ID: {matched_id})")
                            else:
                                print(f"\n  No match for {src_field}: '{m2o_name}' not found in {related_model}")
                        
                        if matched_id:
                            new_vals[dest_field] = matched_id
                        # If no match, skip this field (don't add to new_vals)
                    else:
                        # Non-relational fields - direct copy
                        new_vals[dest_field] = src_value
                
                if not new_vals:
                    print(f"\nSkipping record {vals.get('id')} - no valid field values")
                    migration.skipped_records += 1
                    continue
                
                try:
                    with self.env.cr.savepoint():
                        DestModel.create(new_vals)
                    count += 1
                    migration.migrated_records += 1
                except Exception as e:
                    print(f"\nFailed to create local record for Remote ID {vals.get('id')}: {e}")
                    
                    record_name = vals.get('name') or vals.get('display_name') or f"ID: {vals.get('id')}"
                    error_msg = f"Failed to migrate record: {record_name} (Remote ID: {vals.get('id')}) - Error: {e}"
                    
                    if migration.notes:
                        migration.notes += f"\n{error_msg}"
                    else:
                        migration.notes = error_msg
                    migration.failed_records += 1
                        
                    if not migration.skip_errors:
                        raise
            
            print(f"\nBatch {self.batch} completed. Successfully created {count} records.")
            self.duration = time.time() - start_time
            self.progress = (count / self.limit) * 100 if self.limit else 0.0
            print(f"Duration: {self.duration:.2f} seconds.")
            self.is_done = True
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Batch Completed'),
                    'message': _('Successfully migrated %s records.') % count,
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except Exception as e:
            raise UserError(_("Migration Error: %s") % str(e))


class DataBridgeFieldMapping(models.Model):
    _name = 'data.bridge.field.mapping'
    _description = 'Field Mapping for Migration'

    migration_id = fields.Many2one(
        'data.bridge.api.migration', string='Migration',
        ondelete='cascade', required=True)
    field_name = fields.Char(string='Field Name', required=True)
    field_type = fields.Char(string='Field Type', readonly=True,
        help='Type of the field in the source/old ERP')
    restore_to_id = fields.Many2one(
        'ir.model.fields', string='Restore To',
        domain="[('model_id', '=', parent.destination_model_id)]",
        help='Field in destination model to map this source field to')
    restore_to_type = fields.Selection(
        related='restore_to_id.ttype', string='Restore Type', readonly=True,
        help='Type of the destination field')

    @api.onchange('restore_to_id')
    def _onchange_restore_to_id(self):
        if self.restore_to_id and self.field_type:
            dest_type = self.restore_to_id.ttype
            if self.field_type != dest_type:
                self.restore_to_id = False
                return {
                    'warning': {
                        'title': _('Type Mismatch'),
                        'message': _(
                            'Field types are not compatible!\n'
                            'Source field "%(source)s" has type "%(source_type)s" '
                            'but destination field has type "%(dest_type)s".',
                            source=self.field_name,
                            source_type=self.field_type,
                            dest_type=dest_type
                        ),
                    }
                }
