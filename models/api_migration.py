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
        string='Skip Errors & Continue', default=False, copy=False,
        help='Continue migration even if some records fail')
    
    enable_create = fields.Boolean(
        string='Enable Create', default=False, copy=False,
        help='Enable create mode for migration')
    
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

    def action_update_normal(self):
        """Execute the migration for this specific batch."""
        self.ensure_one()
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
                lambda m: m.restore_to_id and m.field_type not in ['many2one', 'many2many', 'one2many']
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

            ids = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                migration.source_model, 'search',
                [domain],
                {'offset': self.offset, 'limit': self.limit}
            )

            if not ids:
                self.is_done = True
                return

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
            for vals in records:
                old_id = vals.get('id')
                new_vals = {}

                for src_field, info in field_map.items():

                    if src_field not in vals or vals[src_field] in [False, None]:
                        continue

                    src_value = vals[src_field]
                    dest_field = info['dest_field']
                    src_type = info['src_type']
                    dest_type = info['dest_type']

                    print(f"DEBUG → {src_field}: {src_value} ({type(src_value)})")

                    # ----------------------------------------
                    # ✅ MANY2ONE SAFE HANDLING
                    # ----------------------------------------
                    if src_type == 'many2one' and dest_type == 'many2one':

                        m2o_id = False
                        m2o_name = False

                        # 🔥 SAFE EXTRACTION
                        if isinstance(src_value, (list, tuple)):
                            if len(src_value) >= 1:
                                m2o_id = src_value[0]
                            if len(src_value) >= 2:
                                m2o_name = src_value[1]

                        elif isinstance(src_value, int):
                            m2o_id = src_value

                        elif isinstance(src_value, str):
                            m2o_name = src_value.strip()

                        if not m2o_id and not m2o_name:
                            continue

                        field_obj = DestModel._fields.get(dest_field)
                        related_model = field_obj.comodel_name if field_obj else None

                        if not related_model:
                            continue

                        RelatedModel = self.env[related_model].sudo()

                        # 🔥 PARENT FIX
                        if dest_field == 'parent_id':
                            if m2o_id in partner_map:
                                new_vals[dest_field] = partner_map[m2o_id]
                            else:
                                print(f"⚠️ Parent not found yet: {m2o_name}")
                            continue

                        # 🔥 COUNTRY / STATE
                        if related_model in ['res.country', 'res.country.state']:
                            if m2o_name:
                                rec = RelatedModel.search([('name', 'ilike', m2o_name)], limit=1)
                                if rec:
                                    new_vals[dest_field] = rec.id
                            continue

                        # 🔥 GENERIC MATCH
                        if m2o_name:
                            rec = RelatedModel.search([('name', '=', m2o_name)], limit=1)
                            if rec:
                                new_vals[dest_field] = rec.id
                            else:
                                print(f"⚠️ No match for {m2o_name} in {related_model}")

                    # ----------------------------------------
                    # ✅ NORMAL FIELD SAFE
                    # ----------------------------------------
                    else:
                        if isinstance(src_value, (list, tuple)):
                            if len(src_value) >= 2:
                                new_vals[dest_field] = src_value[1]
                            elif len(src_value) == 1:
                                new_vals[dest_field] = src_value[0]
                        else:
                            new_vals[dest_field] = src_value

                # ----------------------------------------
                # ❌ REMOVE BAD FIELDS
                # ----------------------------------------
                new_vals.pop('id', None)
                new_vals.pop('parent_path', None)
                new_vals.pop('complete_name', None)
                new_vals.pop('__last_update', None)

                print(f"\n>>> FINAL VALUES: {new_vals}")

                if not new_vals:
                    migration.skipped_records += 1
                    continue

                # ----------------------------------------
                # ✅ CREATE RECORD
                # ----------------------------------------
                try:
                    with self.env.cr.savepoint():
                        rec = DestModel.create(new_vals)

                    partner_map[old_id] = rec.id

                    count += 1
                    migration.migrated_records += 1

                except Exception as e:
                    print(f"❌ Failed for ID {old_id}: {e}")

                    migration.failed_records += 1
                    migration.notes = (migration.notes or "") + f"\nID {old_id}: {e}"

                    if not migration.skip_errors:
                        raise

            # ----------------------------------------
            # ✅ FINAL
            # ----------------------------------------
            self.duration = time.time() - start_time
            self.progress = (count / self.limit) * 100 if self.limit else 0.0
            self.is_done = True

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Batch Completed',
                    'message': f'Successfully migrated {count} records.',
                    'type': 'success',
                    'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
                },
            }

        except Exception as e:
            raise UserError(f"Migration Error: {str(e)}")

    def action_update_many2one_fields(self):
        """Update Many2one fields safely (DO NOT overwrite existing values)"""
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
            raise Exception("No Many2one fields configured")

        field_map = {
            m.field_name: m.restore_to_id.name
            for m in field_mappings
        }

        source_fields = list(field_map.keys())

        # ----------------------------------------
        # ✅ DEST MODEL
        # ----------------------------------------
        dest_model_name = migration.destination_model_id.model or migration.destination_model
        DestModel = self.env[dest_model_name].sudo()

        # ----------------------------------------
        # ✅ GET RECORDS (BATCHED)
        # ----------------------------------------
        records = DestModel.search([
            ('name', '!=', False)
        ], offset=self.offset, limit=self.limit, order='id asc')

        updated = 0
        skipped_existing = 0
        skipped_not_found = 0

        # ----------------------------------------
        # 🔁 LOOP RECORDS
        # ----------------------------------------
        for rec in records:

            record_name = rec.name
            if not record_name:
                continue

            # ----------------------------------------
            # 🔍 SEARCH SOURCE RECORD
            # ----------------------------------------
            source_ids = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                source_model, 'search',
                [[('name', '=', record_name)]],
                {'limit': 1}
            )

            if not source_ids:
                continue

            source_data = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                source_model, 'read',
                [source_ids],
                {'fields': source_fields}
            )

            if not source_data:
                continue

            source_vals = source_data[0]
            update_vals = {}

            # ----------------------------------------
            # 🔁 MANY2ONE FIELD LOOP
            # ----------------------------------------
            for src_field, dest_field in field_map.items():

                field_obj = DestModel._fields.get(dest_field)

                # ✅ Ensure it's valid many2one field
                if not field_obj or field_obj.type != 'many2one':
                    continue

                # ❌ Skip if already has value
                if rec[dest_field]:
                    skipped_existing += 1
                    continue

                src_value = source_vals.get(src_field)
                if not src_value:
                    continue

                # ----------------------------------------
                # ✅ EXTRACT NAME FROM SOURCE
                # ----------------------------------------
                m2o_name = False

                if isinstance(src_value, (list, tuple)) and len(src_value) >= 2:
                    m2o_name = src_value[1]
                elif isinstance(src_value, str):
                    m2o_name = src_value.strip()

                if not m2o_name:
                    continue

                # ----------------------------------------
                # 🔍 FIND RELATED MODEL
                # ----------------------------------------
                related_model = field_obj.comodel_name
                if not related_model:
                    continue

                RelatedModel = self.env[related_model].sudo()

                # ----------------------------------------
                # 🔎 SEARCH MATCH
                # ----------------------------------------
                rel_rec = RelatedModel.search([
                    '|',
                    ('name', 'ilike', m2o_name),
                    ('display_name', 'ilike', m2o_name)
                ], limit=1)

                # ----------------------------------------
                # ✅ ASSIGN ONLY IF FOUND
                # ----------------------------------------
                if rel_rec:
                    update_vals[dest_field] = rel_rec.id
                elif migration.enable_create:
                    # 🔥 CREATE IF NOT FOUND (ENABLE CREATE MODE)
                    print(f"DEBUG → Creating missing {related_model}: {m2o_name}")
                    new_rel_rec = RelatedModel.create({'name': m2o_name})
                    update_vals[dest_field] = new_rel_rec.id
                else:
                    skipped_not_found += 1

            # ----------------------------------------
            # ✅ SAFE WRITE (NO DATA LOSS)
            # ----------------------------------------
            if update_vals:
                try:
                    # 🛑 Remove invalid values
                    clean_vals = {
                        k: v for k, v in update_vals.items()
                        if v not in (False, None, '', [])
                    }

                    if not clean_vals:
                        continue

                    # 🛡️ Safe write with protected context (PREVENT DATA LOSS)
                    print(f"DEBUG → Updating {rec.name} with {clean_vals}")
                    rec.with_context(
                        tracking_disable=True,
                        mail_notrack=True,
                        mail_create_nolog=True,
                        import_file=True,
                        no_store_function=True
                    ).write(clean_vals)

                    updated += 1

                except Exception as e:
                    print(f"❌ Error updating {rec.name}: {e}")

        # ----------------------------------------
        # ✅ RESULT NOTIFICATION
        # ----------------------------------------
        return {
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
        }

    def action_update_many2many_fields(self):
        """Update Many2many fields safely (ADD to existing values)"""
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
            raise Exception("No Many2many fields configured")

        field_map = {
            m.field_name: m.restore_to_id.name
            for m in field_mappings
        }
        source_fields = list(field_map.keys())

        # ----------------------------------------
        # ✅ DEST MODEL
        # ----------------------------------------
        dest_model_name = migration.destination_model_id.model or migration.destination_model
        DestModel = self.env[dest_model_name].sudo()

        # ----------------------------------------
        # ✅ GET RECORDS (BATCHED)
        # ----------------------------------------
        records = DestModel.search([
            ('name', '!=', False)
        ], offset=self.offset, limit=self.limit, order='id asc')

        updated = 0
        skipped_not_found = 0

        # ----------------------------------------
        # 🔁 LOOP RECORDS
        # ----------------------------------------
        for rec in records:
            record_name = rec.name
            if not record_name:
                continue

            # ----------------------------------------
            # 🔍 SEARCH SOURCE RECORD
            # ----------------------------------------
            source_ids = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                source_model, 'search',
                [[('name', '=', record_name)]],
                {'limit': 1}
            )

            if not source_ids:
                continue

            source_data = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                source_model, 'read',
                [source_ids],
                {'fields': source_fields}
            )

            if not source_data:
                continue

            source_vals = source_data[0]
            update_vals = {}

            # ----------------------------------------
            # 🔁 MANY2MANY FIELD LOOP
            # ----------------------------------------
            for src_field, dest_field in field_map.items():
                field_obj = DestModel._fields.get(dest_field)

                # ✅ Ensure it's valid many2many field
                if not field_obj or field_obj.type != 'many2many':
                    continue

                src_value = source_vals.get(src_field)
                if not src_value or not isinstance(src_value, (list, tuple)):
                    continue

                # ----------------------------------------
                # ✅ EXTRACT NAMES FROM SOURCE M2M IDs
                # ----------------------------------------
                related_model = field_obj.comodel_name
                if not related_model:
                    continue

                m2m_ids = src_value
                # Fetch names from source
                try:
                    m2m_records = models_proxy.execute_kw(
                        migration.erp_db, uid, migration.erp_password,
                        related_model, 'read',
                        [m2m_ids],
                        {'fields': ['name']}
                    )
                except:
                    continue

                if not m2m_records:
                    continue

                # ----------------------------------------
                # 🔎 SEARCH MATCHES LOCALLY
                # ----------------------------------------
                RelatedModel = self.env[related_model].sudo()
                local_ids = []

                for m2m_rec in m2m_records:
                    m2m_name = m2m_rec.get('name')
                    if not m2m_name: continue

                    rel_rec = RelatedModel.search([
                        '|',
                        ('name', '=', m2m_name),
                        ('display_name', '=', m2m_name)
                    ], limit=1)

                    if rel_rec:
                        local_ids.append(rel_rec.id)
                    elif migration.enable_create:
                        print(f"DEBUG → Creating missing {related_model}: {m2m_name}")
                        new_rel_rec = RelatedModel.create({'name': m2m_name})
                        local_ids.append(new_rel_rec.id)
                    else:
                        skipped_not_found += 1

                # ----------------------------------------
                # ✅ ASSIGN (LINK)
                # ----------------------------------------
                if local_ids:
                    # Use (4, id) to add to existing
                    update_vals[dest_field] = [(4, id) for id in local_ids]

            # ----------------------------------------
            # ✅ SAFE WRITE
            # ----------------------------------------
            if update_vals:
                try:
                    print(f"DEBUG → Updating M2M {rec.name} with {update_vals}")
                    rec.with_context(
                        tracking_disable=True,
                        mail_notrack=True,
                        mail_create_nolog=True,
                        import_file=True,
                        no_store_function=True
                    ).write(update_vals)
                    updated += 1
                except Exception as e:
                    print(f"❌ Error updating M2M {rec.name}: {e}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Safe Many2many Update ✅',
                'message': (
                    f'{updated} updated | '
                    f'{skipped_not_found} items not found/skipped'
                ),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
            },
        }

    def action_update_all(self):
        """Execute normal update, Many2one update, and Many2many update in sequence."""
        self.ensure_one()
        self.is_update_all_clicked = True
        
        # 1. Normal Update (Fields without relations)
        self.action_update_normal()
        
        # 2. Many2one Update
        self.action_update_many2one_fields()
        
        # 3. Many2many Update
        self.action_update_many2many_fields()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'All Updates Completed ✅',
                'message': 'Successfully processed normal fields, Many2one, and Many2many components.',
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'full_browser_reload'},
            },
        }

    def action_invisible_btn(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'full_browser_reload',
        }
    
    def action_update_one2many_fields(self):
        pass

class DataBridgeFieldMapping(models.Model):
    _name = 'data.bridge.field.mapping'
    _description = 'Field Mapping for Migration'

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
