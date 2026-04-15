import logging
import xmlrpc.client
import time
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval
from collections import defaultdict
_logger = logging.getLogger(__name__)
import json
# Connection timeout in seconds
CONNECTION_TIMEOUT = 120


class ApiMigration(models.Model):
    _name = 'data.bridge.api.migration'
    _description = 'API Migration from Old ERP'
    _order = 'create_date desc'

    name = fields.Char(string='Migration Name', readonly=True,copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('data.bridge.api.migration') or _('New')
        return super(ApiMigration, self).create(vals_list)
    
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
        string='Skip Errors & Missing', default=False, copy=False,
        help='Continue migration even if some records fail or relations are missing')
    
    enable_create = fields.Boolean(
        string='Enable Create', default=False, copy=False,
        help='while migrating Many2one or Many2Many data , If the data is not there \
         will create with the string where obtained from old ERP')
    
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

        
    def reset_to_connected(self):
        for rec in self:
            rec.state ='connected'

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
        
        # Check for duplicate destination model
        if self.destination_model_id:
            duplicate = self.search([
                ('destination_model_id', '=', self.destination_model_id.id),
                ('id', '!=', self.id)
            ], limit=1)
            if duplicate:
                raise UserError(_(
                    'The Destination Model "%(model)s" is already being used in another migration (%(migration)s).',
                    model=self.destination_model_id.display_name,
                    migration=duplicate.name
                ))
        
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

    def action_auto_update_mappings(self):
        """Automatically map all fields in field_mapping_ids."""
        self.ensure_one()
        if not self.destination_model_id:
            raise UserError(_("Please specify a destination model first."))
        
        self.field_mapping_ids.action_auto_update()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Auto Mapping Completed'),
                'message': _('Field mappings have been automatically updated where matches were found.'),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
            }
        }

    def action_try_map(self):
        pass
        
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
                elif f_type in ('one2many', 'many2many', 'reference'):
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
                field_info = source_fields_all[f_name]

                f_type = field_info.get('type', '')
                f_string = field_info.get('string', '')   # 👈 display name in view

                field_mapping_vals.append((0, 0, {
                    'field_name': f_name,
                    'field_label': f_string,   # 👈 store view name
                    'field_type': f_type,
                    'restore_to_id': False,
                }))

            
            # print("==========================",sorted(source_fields_all.keys()))
            # sfa
            
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
    is_update_all_clicked = fields.Boolean(string='Update All Clicked', default=False, copy=False)
    is_update_normal_clicked = fields.Boolean(string='Update Normal Clicked', default=False, copy=False)
    is_update_m2o_clicked = fields.Boolean(string='Update M2O Clicked', default=False, copy=False)
    is_update_m2m_clicked = fields.Boolean(string='Update M2M Clicked', default=False, copy=False)
    is_update_o2m_clicked = fields.Boolean(string='Update O2M Clicked', default=False, copy=False)
    migrated_record_ids = fields.Text(string='Migrated Record IDs', copy=False)

    def _ensure_marshalable(self, data):
        """Recursively convert defaultdict and unsafe types"""

        # ✅ CRITICAL FIX
        if isinstance(data, defaultdict):
            data = dict(data)

        if isinstance(data, dict):
            return {k: self._ensure_marshalable(v) for k, v in data.items()}

        elif isinstance(data, list):
            return [self._ensure_marshalable(v) for v in data]

        elif isinstance(data, tuple):
            return tuple(self._ensure_marshalable(v) for v in data)

        return data

    def action_update_normal(self):
        """Execute the migration for this specific batch."""
        self.ensure_one()
        _logger.info(">>> Starting Normal Migration for Batch %s (Model: %s)", self.batch, self.migration_id.destination_model_id.name)
        self.is_update_normal_clicked = True
        migration = self.migration_id

        if migration.state != 'running':
            migration.state = 'running'

        if not migration:
            return

        import time
        from odoo.exceptions import UserError
        from odoo.tools.safe_eval import safe_eval

        start_time = time.time()

        try:
            # ----------------------------------------
            # ✅ FIELD MAPPING
            # ----------------------------------------
            field_mappings = migration.field_mapping_ids.filtered(
                lambda m: m.restore_to_id and m.field_type not in ['many2many', 'one2many']
            )

            if not field_mappings:
                raise UserError("No field mappings configured.")

            field_map = {}
            for m in field_mappings:
                field_map[m.field_name] = {
                    'dest_field': m.restore_to_id.name,
                    'src_type': m.field_type,
                    'dest_type': m.restore_to_id.ttype,
                }

            source_fields = list(field_map.keys())

            # ----------------------------------------
            # ✅ CONNECT XMLRPC
            # ----------------------------------------
            common, models_proxy = migration._get_xmlrpc_connection()
            uid = migration.erp_uid

            domain = safe_eval(migration.domain_filter or '[]')
            _logger.info("Fetching record IDs from source model %s with domain %s", migration.source_model, domain)

            _logger.info("Calling execute_kw search on source_model %s...", migration.source_model)
            ids = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                migration.source_model, 'search',
                [domain],
                {'offset': self.offset, 'limit': self.limit}
            )

            if not ids:
                _logger.info("No records found to migrate for batch %s", self.batch)
                self.is_done = True
                return
            
            _logger.info("Found %s records to fetch from old ERP. Calling execute_kw read...", len(ids))

            # ----------------------------------------
            # ✅ FETCH RECORDS
            # ----------------------------------------
            records = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                migration.source_model, 'read',
                [ids],
                {'fields': source_fields}
            )

            if not records:
                return

            # ----------------------------------------
            # ✅ DEST MODEL
            # ----------------------------------------
            dest_model_name = migration.destination_model_id.model or migration.destination_model

            if not dest_model_name:
                raise UserError("Destination model not defined.")

            DestModel = self.env[dest_model_name].with_context(
                tracking_disable=True,
                mail_notrack=True,
                mail_create_nolog=True,
                import_file=True,
            )

            # ----------------------------------------
            # ✅ ID MAP (CRITICAL)
            # ----------------------------------------
            partner_map = {}  # old_id -> new_id

            # ----------------------------------------
            # ✅ SORT (PARENT FIRST)
            # ----------------------------------------
            records = sorted(records, key=lambda r: not r.get('parent_id'))

            count = 0

            # ----------------------------------------
            # 🔁 LOOP RECORDS
            # ----------------------------------------
            vals_list = []
            old_ids_in_batch = []
            for vals in records:
                old_id = vals.get('id')
                new_vals = {}

                for src_field, info in field_map.items():

                    if src_field not in vals or vals[src_field] in [False, None]:
                        continue

                    src_value = vals[src_field]
                    dest_field = info['dest_field']
                    src_type = info['src_type']
                    if src_value is False or src_value is None:
                        continue

                    field_obj = DestModel._fields.get(dest_field)
                    if not field_obj:
                        continue

                    # 🔥 RELATION HANDLING (Many2one)
                    if field_obj.type == 'many2one':
                        related_model = field_obj.comodel_name
                        m2o_name = False
                        if isinstance(src_value, (list, tuple)) and len(src_value) >= 2:
                            m2o_name = src_value[1]
                        elif isinstance(src_value, str):
                            m2o_name = src_value.strip()

                        if not m2o_name:
                            continue

                        # 🔥 COUNTRY / STATE CACHING
                        if related_model in ['res.country', 'res.country.state']:
                            rel_rec = self.env[related_model].sudo().search([('name', 'ilike', m2o_name)], limit=1)
                            if rel_rec:
                                new_vals[dest_field] = rel_rec.id
                            continue

                        # 🔥 GENERIC MATCH
                        RelatedModel = self.env[related_model].sudo()
                        search_domain = []
                        
                        # Use _rec_name or fallback to common field names
                        possible_name_fields = [RelatedModel._rec_name, 'name', 'display_name']
                        seen_fields = set()
                        for f_name in possible_name_fields:
                            if f_name and f_name in RelatedModel._fields and f_name not in seen_fields:
                                seen_fields.add(f_name)
                                part = (f_name, '=', m2o_name)
                                if search_domain:
                                    search_domain = ['|'] + search_domain + [part]
                                else:
                                    search_domain.append(part)
                        
                        if not search_domain:
                            search_domain = [('display_name', '=', m2o_name)]
                        
                        _logger.debug("Searching for Many2one %s with domain %s", related_model, search_domain)
                        rel_rec = RelatedModel.search(search_domain, limit=1)
                        if rel_rec:
                            new_vals[dest_field] = rel_rec.id
                        continue

                    # ✅ NORMAL FIELD
                    else:
                        if isinstance(src_value, (list, tuple)):
                            new_vals[dest_field] = src_value[1] if len(src_value) >= 2 else (src_value[0] if src_value else False)
                        else:
                            new_vals[dest_field] = src_value

                # Cleanup metadata fields
                for f in ['id', 'parent_path', 'complete_name', '__last_update']:
                    new_vals.pop(f, None)

                if new_vals:
                    vals_list.append(new_vals)
                    old_ids_in_batch.append(old_id)
                else:
                    migration.skipped_records += 1

            # ----------------------------------------
            # ✅ BULK CREATE
            # ----------------------------------------
            if vals_list:
                _logger.info("Attempting bulk creation of %s records in destination model %s...", len(vals_list), dest_model_name)
                try:
                    with self.env.cr.savepoint():
                        # Use context to disable heavy triggers
                        new_records = DestModel.with_context(
                            tracking_disable=True,
                            mail_notrack=True,
                            mail_create_nolog=True,
                            import_file=True
                        ).create(vals_list)
                        
                        # Update Map & Stats
                        for i, new_rec in enumerate(new_records):
                            old_id = old_ids_in_batch[i]
                            partner_map[old_id] = new_rec.id
                        
                        count = len(new_records)
                        migration.migrated_records += count
                        _logger.info("Successfully bulk migrated %s records for batch %s.", count, self.batch)

                except Exception as e:
                    _logger.info("❌ Bulk creation failed: %s. Falling back to individual creation...", e)
                    print(f"❌ Bulk creation failed: {e}. Falling back to individual creation...")
                    
                    batch_errors = []
                    for i, individual_vals in enumerate(vals_list):
                        old_id = old_ids_in_batch[i]
                        try:
                            with self.env.cr.savepoint():
                                # Attempt individual create
                                new_rec = DestModel.with_context(
                                    tracking_disable=True,
                                    mail_notrack=True,
                                    mail_create_nolog=True,
                                    import_file=True
                                ).create([individual_vals])
                                
                                partner_map[old_id] = new_rec.id
                                migration.migrated_records += 1
                                count += 1
                                _logger.info("[%s/%s] Successfully migrated record ID %s", count, len(vals_list), old_id)
                        except Exception as rec_e:
                            migration.failed_records += 1
                            error_msg = f"Record ID {old_id} in Batch {self.batch} failed: {str(rec_e)}"
                            _logger.error("❌ [%s/%s] %s", count + 1, len(vals_list), error_msg)
                            migration.notes = (migration.notes or '') + error_msg + "\n"
                            batch_errors.append(error_msg)
                            print(f"❌ {error_msg}")

                    if batch_errors and not migration.skip_errors:
                        error_summary = "\n".join(batch_errors)
                        raise ValidationError(_("Migration failed for some records in Batch %s:\n\n%s") % (self.batch, error_summary))

            # ----------------------------------------
            # ✅ FINAL
            # ----------------------------------------
            self.duration = time.time() - start_time
            self.progress = (count / self.limit) * 100 if self.limit else 0.0
            self.is_done = True

            # 🔥 STORE CREATION DETAILS
            if partner_map:
                new_mappings = [f"[{new_id},{old_id}]" for old_id, new_id in partner_map.items()]
                existing_mappings = (self.migrated_record_ids or '').split(',')
                all_mappings = list(set(filter(None, existing_mappings + new_mappings)))
                self.write({'migrated_record_ids': ','.join(all_mappings)})

            return self._ensure_marshalable({
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Batch Completed',
                    'message': f'Successfully migrated {count} records.',
                    'type': 'success',
                    'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
                },
            })

        except Exception as e:
            _logger.error("Migration Error inside action_update_normal: %s", str(e))
            raise UserError(f"Migration Error (Normal Update): {str(e)}")

    def action_update_many2one_fields(self):
        """Update Many2one fields safely (DO NOT overwrite existing values)"""
        self.ensure_one()
        _logger.info(">>> Starting Many2one Update for Batch %s (Model: %s)", self.batch, self.migration_id.destination_model_id.name)
        self.is_update_m2o_clicked = True
        if self.migration_id.state != 'running':
            self.migration_id.state = 'running'

        if not self.is_update_normal_clicked and not self.is_update_all_clicked:
            raise UserError("You can update Many2one fields only after performing a Normal Update.")

        import xmlrpc.client

        migration = self.migration_id
        if not migration:
            return

        # ----------------------------------------
        # ✅ XMLRPC CONNECTION
        # ----------------------------------------
        common, models_proxy = migration._get_xmlrpc_connection()
        uid = migration.erp_uid

        source_model = migration.source_model

        # ----------------------------------------
        # ✅ MANY2ONE FIELD MAPPING
        # ----------------------------------------
        field_mappings = migration.field_mapping_ids.filtered(
            lambda m: m.restore_to_id and m.field_type == 'many2one'
        )

        if not field_mappings:
            _logger.info("No Many2one fields configured, skipping.")
            return

        field_map = {
            m.field_name: m.restore_to_id.name
            for m in field_mappings
        }

        source_fields = list(field_map.keys())

        # ----------------------------------------
        # ✅ DEST MODEL & BATCH RECORDS
        # ----------------------------------------
        dest_model_name = migration.destination_model_id.model or migration.destination_model
        DestModel = self.env[dest_model_name].sudo()
        local_parents = DestModel.search([
            ('name', '!=', False)
        ], offset=self.offset, limit=self.limit, order='id asc')

        if not local_parents:
            _logger.info("No local records found in this batch range for %s", self.batch)
            return self._notify_reload("No local records found in this batch range.")
        
        _logger.info("Processing %s local records to find matching Many2one values from source ERP.", len(local_parents))

        # ----------------------------------------
        # 🔍 BATCH SEARCH/READ SOURCE PARENTS
        # ----------------------------------------
        parent_names = local_parents.mapped('name')
        _logger.info("Calling execute_kw search on %s for Many2one parents...", source_model)
        source_parent_ids = models_proxy.execute_kw(
            migration.erp_db, uid, migration.erp_password,
            source_model, 'search',
            [[('name', 'in', parent_names)]]
        )

        if not source_parent_ids:
            _logger.info("No matching records found in source ERP for batch %s", self.batch)
            return self._notify_reload("No matching records found in source ERP.")
        
        _logger.info("Found %s matching records in source ERP. Calling execute_kw read for Many2one data...", len(source_parent_ids))

        # Bulk read source data
        source_parents_data = models_proxy.execute_kw(
            migration.erp_db, uid, migration.erp_password,
            source_model, 'read',
            [source_parent_ids],
            {'fields': ['name'] + source_fields}
        )

        # Build Map: name -> source_vals
        source_data_map = {p['name']: p for p in source_parents_data}

        updated = 0
        skipped_existing = 0
        skipped_not_found = 0
        relation_cache = {} # (model, name) -> id

        # ----------------------------------------
        # 🔁 LOOP LOCAL RECORDS & APPLY UPDATES
        # ----------------------------------------
        for rec in local_parents:
            source_vals = source_data_map.get(rec.name)
            if not source_vals:
                continue

            update_vals = {}
            for src_field, dest_field in field_map.items():
                field_obj = DestModel._fields.get(dest_field)
                if not field_obj or field_obj.type != 'many2one':
                    continue

                if rec[dest_field]:
                    skipped_existing += 1
                    continue

                src_value = source_vals.get(src_field)
                if not src_value:
                    continue

                m2o_name = src_value[1] if isinstance(src_value, (list, tuple)) and len(src_value) >= 2 else (src_value.strip() if isinstance(src_value, str) else False)
                if not m2o_name:
                    continue

                related_model = field_obj.comodel_name
                cache_key = (related_model, m2o_name)
                
                if cache_key in relation_cache:
                    rel_id = relation_cache[cache_key]
                else:
                    RelatedModel = self.env[related_model].sudo()
                    search_domain = []
                    possible_name_fields = [RelatedModel._rec_name, 'name', 'display_name']
                    seen_fields = set()
                    for f_name in possible_name_fields:
                        if f_name and f_name in RelatedModel._fields and f_name not in seen_fields:
                            seen_fields.add(f_name)
                            part = (f_name, '=', m2o_name)
                            if search_domain:
                                search_domain = ['|'] + search_domain + [part]
                            else:
                                search_domain.append(part)
                    
                    if not search_domain:
                        search_domain = [('display_name', '=', m2o_name)]

                    rel_rec = RelatedModel.search(search_domain, limit=1)
                    rel_id = rel_rec.id if rel_rec else False
                    
                    if not rel_id and migration.enable_create:
                        rel_rec = RelatedModel.create({'name': m2o_name})
                        rel_id = rel_rec.id
                    
                    relation_cache[cache_key] = rel_id

                if rel_id:
                    update_vals[dest_field] = rel_id
                else:
                    skipped_not_found += 1
                    if migration.skip_errors:
                        msg = f"Batch {self.batch}: {rec.name} -> Missing {related_model} '{m2o_name}'\n"
                        migration.notes = (migration.notes or '') + msg

            if update_vals:
                try:
                    rec.with_context(
                        tracking_disable=True,
                        mail_notrack=True,
                        mail_create_nolog=True,
                        import_file=True,
                        no_store_function=True
                    ).write(update_vals)
                    updated += 1
                    if updated % 10 == 0:
                        _logger.info("Updated %s/%s records with Many2one data...", updated, len(local_parents))
                except Exception as e:
                    _logger.error("❌ Error updating M2O for %s: %s", rec.name, e)
                    print(f"❌ Error updating M2O for {rec.name}: {e}")

        # ----------------------------------------
        # ✅ RESULT NOTIFICATION
        # ----------------------------------------
        return self._ensure_marshalable({
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Safe Many2one Update ✅',
                'message': (
                    f'{updated} updated | '
                    f'{skipped_existing} skipped (already had values) | '
                    f'{skipped_not_found} not found'
                ),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
            },
        })

    def action_update_many2many_fields(self):
        """Update Many2many fields safely (ADD to existing values)"""
        self.ensure_one()
        _logger.info(">>> Starting Many2many Update for Batch %s (Model: %s)", self.batch, self.migration_id.destination_model_id.name)
        self.is_update_m2m_clicked = True
        if self.migration_id.state != 'running':
            self.migration_id.state = 'running'

        if not self.is_update_normal_clicked and not self.is_update_all_clicked:
            raise UserError("You can update Many2many fields only after performing a Normal Update.")

        import xmlrpc.client

        migration = self.migration_id
        if not migration:
            return

        # ----------------------------------------
        # ✅ XMLRPC CONNECTION
        # ----------------------------------------
        common, models_proxy = migration._get_xmlrpc_connection()
        uid = migration.erp_uid
        source_model = migration.source_model

        # ----------------------------------------
        # ✅ MANY2MANY FIELD MAPPING
        # ----------------------------------------
        field_mappings = migration.field_mapping_ids.filtered(
            lambda m: m.restore_to_id and m.field_type == 'many2many'
        )

        if not field_mappings:
            _logger.info("No Many2many fields configured, skipping.")
            return

        field_map = {
            m.field_name: m.restore_to_id.name
            for m in field_mappings
        }
        source_fields = list(field_map.keys())

        # ----------------------------------------
        # ✅ DEST MODEL & BATCH RECORDS
        # ----------------------------------------
        dest_model_name = migration.destination_model_id.model or migration.destination_model
        DestModel = self.env[dest_model_name].sudo()
        local_parents = DestModel.search([
            ('name', '!=', False)
        ], offset=self.offset, limit=self.limit, order='id asc')

        if not local_parents:
            return self._notify_reload("No local records found in this batch range.")

        # ----------------------------------------
        # 🔍 BATCH SEARCH/READ SOURCE PARENTS
        # ----------------------------------------
        parent_names = local_parents.mapped('name')
        _logger.info("Calling execute_kw search on %s for Many2many parents...", source_model)
        source_parent_ids = models_proxy.execute_kw(
            migration.erp_db, uid, migration.erp_password,
            source_model, 'search',
            [[('name', 'in', parent_names)]]
        )

        if not source_parent_ids:
            _logger.info("No matching records found in source ERP for batch %s", self.batch)
            return self._notify_reload("No matching records found in source ERP.")
        
        _logger.info("Found %s matching records in source ERP. Calling execute_kw read for Many2many data...", len(source_parent_ids))

        # Bulk read source data (M2M fields)
        source_parents_data = models_proxy.execute_kw(
            migration.erp_db, uid, migration.erp_password,
            source_model, 'read',
            [source_parent_ids],
            {'fields': ['name'] + source_fields}
        )

        # Build Map: name -> source_vals
        source_data_map = {p['name']: p for p in source_parents_data}

        updated = 0
        skipped_not_found = 0
        relation_cache = {} # (model, name) -> id

        # ----------------------------------------
        # 🔁 LOOP LOCAL RECORDS & APPLY UPDATES
        # ----------------------------------------
        for rec in local_parents:
            source_vals = source_data_map.get(rec.name)
            if not source_vals:
                continue

            update_vals = {}
            for src_field, dest_field in field_map.items():
                field_obj = DestModel._fields.get(dest_field)
                if not field_obj or field_obj.type != 'many2many':
                    continue

                src_m2m_data = source_vals.get(src_field)
                if not src_m2m_data or not isinstance(src_m2m_data, list):
                    continue

                related_model = field_obj.comodel_name
                RelatedModel = self.env[related_model].sudo()
                
                # Fetch target record names if we only have IDs
                target_ids = src_m2m_data
                _logger.info("Calling execute_kw read on related model %s for M2M targets %s...", related_model, target_ids)
                target_records_data = models_proxy.execute_kw(
                    migration.erp_db, uid, migration.erp_password,
                    related_model, 'read',
                    [target_ids],
                    {'fields': ['display_name', 'name']}
                )

                local_target_ids = []
                for t_data in target_records_data:
                    t_name = t_data.get('display_name') or t_data.get('name')
                    if not t_name: continue
                    
                    cache_key = (related_model, t_name)
                    if cache_key in relation_cache:
                        l_id = relation_cache[cache_key]
                    else:
                        rel_rec = RelatedModel.search([
                            '|', ('name', '=', t_name), ('display_name', '=', t_name)
                        ], limit=1)
                        l_id = rel_rec.id if rel_rec else False
                        relation_cache[cache_key] = l_id
                    
                    if l_id:
                        local_target_ids.append(l_id)
                    else:
                        skipped_not_found += 1
                        if migration.skip_errors:
                            msg = f"Batch {self.batch}: {rec.name} -> Missing M2M target {related_model} '{t_name}'\n"
                            migration.notes = (migration.notes or '') + msg

                if local_target_ids:
                    # Odoo M2M update: (4, id) to add links
                    update_vals[dest_field] = [(4, tid) for tid in local_target_ids if tid not in rec[dest_field].ids]

            if update_vals:
                try:
                    rec.with_context(
                        tracking_disable=True,
                        mail_notrack=True,
                        mail_create_nolog=True,
                        import_file=True,
                        no_store_function=True
                    ).write(update_vals)
                    updated += 1
                    if updated % 10 == 0:
                        _logger.info("Updated %s/%s records with Many2many data...", updated, len(local_parents))
                except Exception as e:
                    _logger.error("❌ Error updating M2M for %s: %s", rec.name, e)
                    print(f"❌ Error updating M2M for {rec.name}: {e}")

        # ----------------------------------------
        # ✅ RESULT NOTIFICATION
        # ----------------------------------------
        return self._ensure_marshalable({
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Safe Many2many Update ✅',
                'message': f'{updated} updated | {skipped_not_found} not found',
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
            },
        })

    def action_update_all(self):
        """Execute normal update, Many2one update, and Many2many update in sequence."""
        self.ensure_one()
        _logger.info(">>> Starting FULL UPDATE for Batch %s (Model: %s)", self.batch, self.migration_id.destination_model_id.name)
        self.is_update_all_clicked = True
        
        # 1. Normal Update (Fields without relations)
        self.action_update_normal()
        
        # 2. Many2one Update
        self.action_update_many2one_fields()
        
        # 3. Many2many Update
        self.action_update_many2many_fields()
        
        return self._ensure_marshalable({
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'All Updates Completed ✅',
                'message': 'Successfully processed normal fields, Many2one, and Many2many components.',
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
            },
        })

    def action_invisible_btn(self):
        pass
        
    def action_update_one2many_fields(self):
        """Open dynamic wizard to select which One2many field to update and map its fields."""
        self.ensure_one()
        return {
            'name': _('Dynamic One2many Migration'),
            'type': 'ir.actions.act_window',
            'res_model': 'data.bridge.o2m.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_migrator_id': self.id,
            }
        }

    def _run_o2m_migration(self, src_o2m, dest_o2m_name, mappings, source_child_model=False):
        """Update a specific One2many field using dynamic mappings from the wizard."""
        self.ensure_one()
        _logger.info(">>> Starting Dynamic One2many Update for Batch %s (Source: %s -> Dest: %s)", self.batch, src_o2m, dest_o2m_name)
        
        self.is_update_o2m_clicked = True
        migration = self.migration_id

        # 1. XMLRPC Connection
        common, models_proxy = migration._get_xmlrpc_connection()
        uid = migration.erp_uid
        source_model = migration.source_model
        erp_db = migration.erp_db
        erp_password = migration.erp_password

        # 2. Field Analysis & Validation
        # mappings is [(src_field, dest_field_name), ...]
        field_map = {src: dest for src, dest in mappings}
        source_o2m_fields = list(field_map.keys())

        # Verify Source O2M field exists on source parent model
        try:
            source_fields_info = models_proxy.execute_kw(
                erp_db, uid, erp_password,
                source_model, 'fields_get',
                [src_o2m],
                {'attributes': ['type']}
            )
            if not source_fields_info or src_o2m not in source_fields_info:
                 return self._notify_reload(f"Field '{src_o2m}' not found on source model '{source_model}' in ERP. Please check the 'Source O2M Field' name in wizard.")
        except Exception as e:
             _logger.warning("Could not validate source O2M field: %s", e)
             # We proceed but we might fail later with the ValueError the user saw.
             # Actually, if we're here, we're likely going to fail. 
             # Let's be more descriptive if it fails later.
        
        # 3. Parse [new_id,old_id] pairs
        if not self.migrated_record_ids:
            return self._notify_reload("No records migrated yet in this batch. Update Normal first.")

        # Build map: source_id -> local_id
        source_id_to_local_id = {}
        raw_entries = self.migrated_record_ids.split('],[')
        for entry in raw_entries:
            clean_entry = entry.replace('[', '').replace(']', '')
            if clean_entry:
                parts = clean_entry.split(',')
                if len(parts) >= 2:
                    try:
                        l_id = int(parts[0])
                        s_id = int(parts[1])
                        source_id_to_local_id[s_id] = l_id
                    except:
                        continue

        if not source_id_to_local_id:
            return self._notify_reload("No valid ID mappings found in this batch.")

        dest_model_name = migration.destination_model_id.model
        DestModel = self.env[dest_model_name].sudo()
        local_parents = DestModel.browse(source_id_to_local_id.values()).filtered(lambda r: r.exists())

        if not local_parents:
            return self._notify_reload("No valid local records found for the migrated IDs.")

        # 4. Fetch Source Parents from Old ERP using IDs
        source_parent_ids = list(source_id_to_local_id.keys())
        _logger.info("Fetching One2many data for %s from source records %s...", src_o2m, source_parent_ids)

        source_parents_data = models_proxy.execute_kw(
            erp_db, uid, erp_password,
            source_model, 'read',
            [source_parent_ids],
            {'fields': ['id', src_o2m]}
        )

        total_children_created = 0

        # 6. Process the O2M Field
        DestChildModelName = DestModel._fields[dest_o2m_name].comodel_name
        LocalChildModel = self.env[DestChildModelName].sudo()
        SrcChildModel = source_child_model or DestChildModelName
        
        # Find the inverse Many2one field
        inverse_field = DestModel._fields[dest_o2m_name].inverse_name
        if not inverse_field:
             return self._notify_reload(f"Inverse field not found for {dest_o2m_name}")

        # Collect all child IDs for this O2M field across all parents
        all_source_child_ids = []
        child_to_parent_map = {} # source_child_id -> local_parent_id
        
        for s_parent in source_parents_data:
            s_child_ids = s_parent.get(src_o2m)
            if s_child_ids and isinstance(s_child_ids, list):
                all_source_child_ids.extend(s_child_ids)
                l_parent_id = source_id_to_local_id.get(s_parent['id'])
                for sc_id in s_child_ids:
                    child_to_parent_map[sc_id] = l_parent_id

        if not all_source_child_ids:
             return self._notify_reload("No child records found to migrate.")

        # Bulk Read Source Children from SrcChildModel
        source_children_data = models_proxy.execute_kw(
            erp_db, uid, erp_password,
            SrcChildModel, 'read',
            [all_source_child_ids],
            {'fields': source_o2m_fields}
        )

        # 7. Prepare Bulk Creation
        child_vals_list = []
        relation_cache = {} # (model, name) -> id
        child_fields_obj = LocalChildModel._fields

        for s_child in source_children_data:
            child_vals = {}
            # Use mappings from wizard
            for s_f, d_f in field_map.items():
                value = s_child.get(s_f)
                if value is None or value is False:
                    continue
                
                field_obj = child_fields_obj.get(d_f)
                if not field_obj:
                    continue

                # 🔥 MANY2ONE HANDLING
                if field_obj.type == 'many2one':
                    m2o_name = value[1] if isinstance(value, (list, tuple)) and len(value) >= 2 else (value.strip() if isinstance(value, str) else False)
                    if not m2o_name:
                        continue
                    
                    related_model = field_obj.comodel_name
                    cache_key = (related_model, m2o_name)
                    
                    if cache_key in relation_cache:
                        rel_id = relation_cache[cache_key]
                    else:
                        RelatedModel = self.env[related_model].sudo()
                        rel_rec = RelatedModel.search(['|', ('name', '=', m2o_name), ('display_name', '=', m2o_name)], limit=1)
                        rel_id = rel_rec.id if rel_rec else False
                        
                        if not rel_id and migration.enable_create:
                            rel_rec = RelatedModel.create({'name': m2o_name})
                            rel_id = rel_rec.id
                        
                        relation_cache[cache_key] = rel_id
                    
                    if rel_id:
                        child_vals[d_f] = rel_id

                # 🔥 MANY2MANY HANDLING
                elif field_obj.type == 'many2many':
                    if not isinstance(value, list):
                        continue
                    
                    related_model = field_obj.comodel_name
                    RelatedModel = self.env[related_model].sudo()
                    
                    # Read target names from ERP
                    try:
                        target_data = models_proxy.execute_kw(
                            erp_db, uid, erp_password,
                            related_model, 'read',
                            [value],
                            {'fields': ['name', 'display_name']}
                        )
                    except:
                        continue
                    
                    local_ids = []
                    for t_vals in target_data:
                        t_name = t_vals.get('display_name') or t_vals.get('name')
                        if not t_name: continue
                        
                        cache_key = (related_model, t_name)
                        if cache_key in relation_cache:
                            l_id = relation_cache[cache_key]
                        else:
                            rel_rec = RelatedModel.search(['|', ('name', '=', t_name), ('display_name', '=', t_name)], limit=1)
                            l_id = rel_rec.id if rel_rec else False
                            
                            if not l_id and migration.enable_create:
                                rel_rec = RelatedModel.create({'name': t_name})
                                l_id = rel_rec.id
                            
                            relation_cache[cache_key] = l_id
                        
                        if l_id:
                            local_ids.append(l_id)
                    
                    if local_ids:
                        child_vals[d_f] = [(6, 0, local_ids)]

                # ✅ NORMAL FIELD
                else:
                    if isinstance(value, (list, tuple)) and len(value) == 2:
                        child_vals[d_f] = value[1]
                    else:
                        child_vals[d_f] = value
            
            # Set the Parent Relation
            local_p_id = child_to_parent_map.get(s_child['id'])
            if local_p_id:
                child_vals[inverse_field] = local_p_id
                child_vals_list.append(child_vals)

        # 8. ORM Create
        if child_vals_list:
            try:
                with self.env.cr.savepoint():
                    new_children = LocalChildModel.with_context(tracking_disable=True).create(child_vals_list)
                    total_children_created = len(new_children)
            except Exception as e:
                _logger.info("❌ Bulk O2M creation failed for %s: %s. Falling back to individual creation...", dest_o2m_name, e)
                total_children_created = 0
                for c_vals in child_vals_list:
                    try:
                        with self.env.cr.savepoint():
                            LocalChildModel.with_context(tracking_disable=True).create([c_vals])
                            total_children_created += 1
                    except Exception as rec_e:
                        if migration.skip_errors:
                            error_msg = f"Batch {self.batch}: O2M {dest_o2m_name} child creation failed: {str(rec_e)}\n"
                            migration.notes = (migration.notes or '') + error_msg
                            continue
                        else:
                            raise ValidationError(_("O2M Migration failed for a child record in %s field:\n%s") % (dest_o2m_name, str(rec_e)))

        return self._notify_reload(f"Dynamic O2M completed for {dest_o2m_name}. Created {total_children_created} child records.")

    def _notify_reload(self, message):
        return self._safe_return({
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Migration Update ✅',
                'message': message,
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
            }
        })
    def _safe_return(self, data):
        """Final XML-RPC safe return"""
        return json.loads(json.dumps(self._ensure_marshalable(data)))


    def action_view_details(self):
        """Open the list of migrated records for this batch."""
        self.ensure_one()
        if not self.migrated_record_ids:
            return self._notify_reload(_("No records migrated yet."))
            
        # Parse [new_id,old_id],[new_id,old_id]... format
        record_ids = []
        if self.migrated_record_ids:
            raw_entries = self.migrated_record_ids.split('],[')
            for entry in raw_entries:
                clean_entry = entry.replace('[', '').replace(']', '')
                if clean_entry:
                    parts = clean_entry.split(',')
                    if parts:
                        try:
                            record_ids.append(int(parts[0]))
                        except:
                            continue

        dest_model_name = self.migration_id.destination_model_id.model or self.migration_id.destination_model
        
        return {
            'name': _('Migrated Records'),
            'type': 'ir.actions.act_window',
            'res_model': dest_model_name,
            'view_mode': 'list,form',
            'domain': [('id', 'in', record_ids)],
            'target': 'current',
        }

class DataBridgeFieldMapping(models.Model):
    _name = 'data.bridge.field.mapping'
    _description = 'Field Mapping for Migration'
    _rec_name = 'field_label'

    migration_id = fields.Many2one(
        'data.bridge.api.migration', string='Migration',
        ondelete='cascade', required=True)

    field_label = fields.Char(string='Data From (Old ERP)' )
    field_name = fields.Char(string='Technical Name', required=True)

    field_type = fields.Char(string='Field Type', readonly=True,
        help='Type of the field in the source/old ERP')
    restore_to_id = fields.Many2one(
        'ir.model.fields', string='Restore To (New ERP)',
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

    def action_auto_update(self):
        """Automatically find a matching destination field based on name and type."""
        for rec in self:
            if not rec.migration_id.destination_model_id:
                continue
            
            # Search for a field in the destination model with the same name and type
            domain = [
                ('model_id', '=', rec.migration_id.destination_model_id.id),
                ('name', '=', rec.field_name),
                ('ttype', '=', rec.field_type),
            ]
            matching_field = self.env['ir.model.fields'].search(domain, limit=1)
            
            if matching_field:
                rec.restore_to_id = matching_field.id
            else:
                # Optional: Try matching by label if name doesn't match?
                # For now, just name and type is safer.
                pass
