import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class DataBridgeO2mWizard(models.TransientModel):
    _name = 'data.bridge.o2m.wizard'
    _description = 'Dynamic One2many Migration Wizard'

    migrator_id = fields.Many2one('migrator.migrator', string='Batch')
    
    migration_id = fields.Many2one(
        'data.bridge.api.migration', string='Migration',
        related='migrator_id.migration_id'
    )
    
    destination_model_id = fields.Many2one(
        'ir.model', string='Destination Model', 
        related='migrator_id.migration_id.destination_model_id'
    )
    
    source_o2m_field = fields.Char(string='Source O2M Field (ERP)')
    
    o2m_field_id = fields.Many2one(
        'ir.model.fields', string='Destination O2M Field', 
        domain="[('model_id', '=', destination_model_id), ('ttype', '=', 'one2many')]"
    )

    child_model_id = fields.Many2one(
        'ir.model', string='Child Model',
        compute='_compute_child_model', store=True
    )
    
    source_child_model = fields.Char(
        string='Source Child Model (ERP)', 
        help="Model name of the child records in the source ERP (e.g. sale.order.line)"
    )
    
    migrated_record_ids_display = fields.Text(string='IDs of Records', related='migrator_id.migrated_record_ids')
    source_model_display = fields.Char(string='Source Model', related='migration_id.source_model')
    enable_create = fields.Boolean(
        string='Enable Create', 
        related='migration_id.enable_create', 
        readonly=False,
        help="If enabled, missing relational records (Many2one/Many2many) will be created during migration."
    )



    field_mapping_o2m = fields.One2many(
        'data.bridge.o2m.wizard.line', 'wizard_id', 
        string='Field Mappings'
    )

    destination_model = fields.Char(string="Destination Model",related='o2m_field_id.relation')

    @api.depends('o2m_field_id')
    def _compute_child_model(self):
        for rec in self:
            if rec.o2m_field_id:
                child_model_name = rec.o2m_field_id.relation
                rec.child_model_id = self.env['ir.model'].search([('model', '=', child_model_name)], limit=1)
            else:
                rec.child_model_id = False

    @api.onchange('o2m_field_id', 'source_o2m_field')
    def _onchange_fetch_fields(self):
        if self.o2m_field_id and self.source_o2m_field:
             self.action_fetch_fields()

    def action_fetch_fields(self):
        self.ensure_one()
        migration = self.migration_id
        common, models_proxy = migration._get_xmlrpc_connection()
        uid = migration.erp_uid
        erp_db = migration.erp_db
        erp_password = migration.erp_password
        
        # 1. Determine Source Child Model
        s_child_model = self.source_child_model
        if not s_child_model and self.source_o2m_field:
            s_field = self.source_o2m_field.strip()
            try:
                all_source_fields = models_proxy.execute_kw(
                    erp_db, uid, erp_password,
                    migration.source_model, 'fields_get',
                    [],
                    {'attributes': ['relation', 'type']}
                )
                
                if s_field in all_source_fields:
                    o2m_info = all_source_fields[s_field]
                    if o2m_info['type'] in ['one2many', 'many2many']:
                        s_child_model = o2m_info.get('relation')
                        self.source_child_model = s_child_model
                else:
                    # Smart Suggestion: Maybe they put the model name?
                    potential_fields = [fn for fn, fi in all_source_fields.items() 
                                       if fi.get('relation') == s_field and fi.get('type') in ['one2many', 'many2many']]
                    if potential_fields:
                         raise UserError(_("Field '%s' not found on %s. Did you mean '%s'?") % 
                                         (s_field, migration.source_model, potential_fields[0]))
                    else:
                         raise UserError(_("Field '%s' not found on %s. Please check the field name in your old ERP.") % 
                                         (s_field, migration.source_model))
            except UserError:
                raise
            except Exception as e:
                _logger.warning("Auto-discovery failed: %s", e)
        
        if not s_child_model:
             raise UserError(_("Please provide a 'Source Child Model (ERP)' or a valid 'Source O2M Field' to discover it."))

        # 2. Get All Fields of Source Child Model from ERP
        try:
            source_fields = models_proxy.execute_kw(
                erp_db, uid, erp_password,
                s_child_model, 'fields_get',
                [],
                {'attributes': ['type', 'string']}
            )
        except Exception as e:
            raise UserError(_("Could not fetch fields for model '%s' from ERP: %s") % (s_child_model, str(e)))
        
        # 3. Get Local Fields of Child Model
        if not self.child_model_id:
             raise UserError(_("Please select a 'Destination O2M Field' first."))

        LocalChildFields = self.env['ir.model.fields'].search([
            ('model_id', '=', self.child_model_id.id),
            ('ttype', 'not in', ['one2many', 'many2many'])
        ])
        local_field_map = {f.name: f.id for f in LocalChildFields}
        
        # 4. Populate Lines
        self.field_mapping_o2m = [(5, 0, 0)]  # Clear existing lines
        
        lines_to_create = []
        for f_name, f_info in source_fields.items():
            if f_name in ['id', 'create_uid', 'create_date', 'write_uid', 'write_date']:
                continue
            
            lines_to_create.append((0, 0, {
                'source_field': f_name,
                'dest_field_id': local_field_map.get(f_name, False)
            }))
        
        if lines_to_create:
            self.field_mapping_o2m = lines_to_create
            
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'data.bridge.o2m.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def action_fetch_fields_by_dest_model(self):
        self.ensure_one()
        if not self.destination_model:
             raise UserError(_("Please select a Destination O2M Field first."))
        
        migration = self.migration_id
        common, models_proxy = migration._get_xmlrpc_connection()
        uid = migration.erp_uid
        erp_db = migration.erp_db
        erp_password = migration.erp_password
        
        # Use destination_model as the source model name in old ERP
        source_model = self.destination_model
        
        try:
            source_fields = models_proxy.execute_kw(
                erp_db, uid, erp_password,
                source_model, 'fields_get',
                [],
                {'attributes': ['type', 'string']}
            )
        except Exception as e:
            raise UserError(_("Could not fetch fields for model '%s' from ERP: %s") % (source_model, str(e)))

        self.field_mapping_o2m = [(5, 0, 0)]
        lines_to_create = []
        for f_name, f_info in source_fields.items():
            if f_name in ['id', 'create_uid', 'create_date', 'write_uid', 'write_date']:
                continue
            lines_to_create.append((0, 0, {
                'source_field': f_name,
                'dest_field_id': False  # Always empty as per user request
            }))
        
        if lines_to_create:
            self.field_mapping_o2m = lines_to_create
            
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'data.bridge.o2m.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def action_update(self):
        self.ensure_one()
        if not self.o2m_field_id:
            raise UserError(_("Please select a One2many field."))
        if not self.field_mapping_o2m:
            raise UserError(_("Please configure at least one field mapping."))
        
        # Build mappings list: [(source_field, dest_field_name)]
        mappings = []
        for line in self.field_mapping_o2m:
            src_f = (line.source_field or '').strip()
            dest_f = line.dest_field_id.name
            if src_f and dest_f:
                mappings.append((src_f, dest_f))
        
        # Determine source O2M field name (fallback to destination field name if empty)
        src_o2m = (self.source_o2m_field or '').strip() or self.o2m_field_id.name
        
        return self.migrator_id._run_o2m_migration(
            src_o2m, 
            self.o2m_field_id.name, 
            mappings, 
            source_child_model=self.source_child_model
        )

class DataBridgeO2mWizardLine(models.TransientModel):
    _name = 'data.bridge.o2m.wizard.line'
    _description = 'O2M Field Mapping Line'

    wizard_id = fields.Many2one('data.bridge.o2m.wizard', string='Wizard')
    child_model_id = fields.Many2one('ir.model', related='wizard_id.child_model_id')
    
    source_field = fields.Char(string='Source Field (ERP)')
    dest_field_id = fields.Many2one(
        'ir.model.fields', string='Destination Field',
        domain="[('model_id', '=', child_model_id)]"
    )
