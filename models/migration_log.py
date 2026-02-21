from odoo import models, fields, api


class MigrationLog(models.Model):
    _name = 'data.bridge.migration.log'
    _description = 'Migration Log'
    _order = 'create_date desc'
    _rec_name = 'message'

    api_migration_id = fields.Many2one(
        'data.bridge.api.migration', string='API Migration',
        ondelete='cascade', index=True)
    sql_migration_id = fields.Many2one(
        'data.bridge.sql.migration', string='SQL Migration',
        ondelete='cascade', index=True)
    
    migration_type = fields.Selection([
        ('api', 'API Migration'),
        ('sql', 'SQL File Migration'),
    ], string='Migration Type', compute='_compute_migration_type', store=True)
    
    migration_date = fields.Datetime(
        string='Date', default=fields.Datetime.now, index=True)
    
    log_type = fields.Selection([
        ('info', 'Information'),
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ], string='Type', default='info', index=True)
    
    message = fields.Text(string='Message')
    source_data = fields.Text(string='Source Data')
    error_details = fields.Text(string='Error Details')
    
    is_batch_summary = fields.Boolean(string='Is Summary', index=True)
    records_created = fields.Integer(string='Records Created')
    records_failed = fields.Integer(string='Records Failed')

    @api.depends('api_migration_id', 'sql_migration_id')
    def _compute_migration_type(self):
        for record in self:
            if record.api_migration_id:
                record.migration_type = 'api'
            elif record.sql_migration_id:
                record.migration_type = 'sql'
            else:
                record.migration_type = False

    def action_view_source_data(self):
        """View source data in a popup."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Source Data',
            'res_model': 'data.bridge.migration.log',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'show_source_data': True},
        }
