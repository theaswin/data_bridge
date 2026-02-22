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

    name = fields.Char(string='Migration Name', required=True, copy=False)
    
    # Connection Details
    erp_url = fields.Char(
        string='Old ERP URL', required=True, copy=False,
        help='URL of the old Odoo ERP (e.g., https://old-erp.example.com)')
    erp_db = fields.Char(
        string='Database Name', required=True, copy=False,
        help='Database name of the old ERP')
    erp_username = fields.Char(
        string='Username', required=True, copy=False,
        help='Login username (email) for the old ERP')
    erp_password = fields.Char(
        string='Password', required=True, copy=False)
    erp_uid = fields.Integer(
        string='User ID', readonly=True, copy=False,
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
        string='Source Model', required=True, copy=False,
        help='Model name in old ERP (e.g., res.partner, product.product)')
    destination_model = fields.Char(
        string='Destination Model', required=True, copy=False,
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
        string='Progress', default=0, readonly=True, copy=False,
        help='Migration progress percentage')
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
        string='Duration (seconds)', readonly=True, copy=False)
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
    batch = fields.Integer(string="Batch", default=0, copy=False)
    duration = fields.Float(string="Duration", default=0, copy=False)
    destination_model_id = fields.Many2one(
        'ir.model', string='Destination Model', copy=False)

        

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
        url = self.erp_url
        db = self.erp_db
        username = self.erp_username
        password = self.erp_password

        try:
            print(f"Connecting to {url}...")
            # Common endpoint (authentication)
            common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")

            uid = common.authenticate(db, username, password, {})

            if not uid:
                print("Authentication failed.")
                return

            print(f"Logged in. UID: {uid}")

            # Object endpoint
            models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

            print(f"Fetching count for {self.source_model}...")
            partner_count = models.execute_kw(
                db,
                uid,
                password,
                self.source_model,
                'search_count',
                [[]]   # empty domain = all records
            )
            print(f"Total records found: {partner_count}")

            self.write({
                'total_source_records': partner_count,
                'state': 'ready',
            })

        except Exception as e:
            raise ValidationError("Failed To Fetch Table Details")

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





class MigratorMigrator(models.Model):
    _name = 'migrator.migrator'
    _description = 'Migrator Batch'

    migration_id = fields.Many2one('data.bridge.api.migration', string='Migration', ondelete='cascade', copy=False)
    batch = fields.Integer(string='Batch', copy=False)
    limit = fields.Integer(string='Limit', copy=False)
    offset = fields.Integer(string='Offset', copy=False)
    data_range = fields.Char(string='Data Range', copy=False)
    duration = fields.Float(string='Duration (seconds)', copy=False)
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
            print("Connecting to source ERP...")
            common, models_proxy = migration._get_xmlrpc_connection()
            uid = migration.erp_uid
            
            # 1. Fetch Records from Old ERP
            print(f"Searching for records in {migration.source_model}...")
            ids = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                migration.source_model, 'search',
                [[]], # Empty domain for now
                {'offset': self.offset, 'limit': self.limit}
            )
            
            if not ids:
                print("No records found in this range.")
                return

            # 2. Get Local Model & Common Fields
            DestModel = self.env[migration.destination_model_id.model]
            dest_fields_list = list(DestModel._fields.keys())
            
            # Fetch source fields once per batch or use parent's metadata if we had it
            # To be safe and fix the "Expected Singleton" issue, we only want to fetch fields 
            # that we actually need and exist on both sides.
            print("Fetching source field metadata to avoid crashing on computed fields...")
            source_fields_all = models_proxy.execute_kw(
                migration.erp_db, uid, migration.erp_password,
                migration.source_model, 'fields_get',
                [], {'attributes': ['type', 'store']}
            )
            
            # Only migrate fields that:
            # - Exist in both source and destination
            # - Are not relational (M2O, O2M, M2M) to keep it simple as requested
            # - Are stored in source (to avoid broken computed fields on remote)
            common_fields = [
                f for f in source_fields_all 
                if f in dest_fields_list 
                and f != 'id'
                and source_fields_all[f].get('type') not in ('many2one', 'one2many', 'many2many')
                and source_fields_all[f].get('store', True) # Prefer stored fields
            ]
            
            print(f"Common stored fields identified: {len(common_fields)}")
            
            records = []
            try:
                print(f"Attempting batch read of {len(ids)} records...")
                records = models_proxy.execute_kw(
                    migration.erp_db, uid, migration.erp_password,
                    migration.source_model, 'read',
                    [ids],
                    {'fields': common_fields}
                )
            except Exception as read_err:
                print(f"Batch read failed: {read_err}. Falling back to one-by-one reading...")
                # Fallback: Read records one by one if remote has broken computed fields
                for i, rid in enumerate(ids):
                    print(f"Reading record {i+1}/{len(ids)} (ID: {rid})...", end="\r")
                    try:
                        record_data = models_proxy.execute_kw(
                            migration.erp_db, uid, migration.erp_password,
                            migration.source_model, 'read',
                            [[rid]],
                            {'fields': common_fields}
                        )
                        if record_data:
                            records.append(record_data[0])
                    except Exception as single_err:
                        print(f"\nFailed to read record {rid}: {single_err}")
                print("\nOne-by-one reading completed.")

            if not records:
                print("No record data could be retrieved.")
                return
                
            print(f"Fetched {len(records)} records. Creating locally...")
            
            # 3. Creation
            count = 0
            for i, vals in enumerate(records):
                print(f"Processing record {i+1}/{len(records)} (Remote ID: {vals.get('id')})...", end="\r")
                new_vals = {f: vals[f] for f in common_fields if f in vals}
                
                try:
                    DestModel.create(new_vals)
                    count += 1
                except Exception as e:
                    print(f"\nFailed to create local record for Remote ID {vals.get('id')}: {e}")
                    if not migration.skip_errors:
                        raise
            
            print(f"\nBatch {self.batch} completed. Successfully created {count} records.")
            self.duration = time.time() - start_time
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
