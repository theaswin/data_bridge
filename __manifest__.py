{
    'name': 'Data Bridge',
    'version': '19.0.1.0.0',
    'category': 'Tools',
    'summary': 'High-performance data migration from external ERP or SQL files',
    'description': """
Data Bridge - High Performance Database Migration Tool
======================================================

Migrate large volumes of data (10,000 - 100,000+ records) safely and efficiently.

Method 1: API Migration
-----------------------
* Connect to old Odoo ERP using URL, Database, Username, and Password
* Fetch data via XML-RPC API
* Optimized batch processing with bulk create
* Real-time progress tracking
* Automatic exclusion of Many2many and Many2one fields

Method 2: SQL File Migration  
----------------------------
* Upload .sql database dump file
* Select source table and destination Odoo model
* High-performance ORM-based migration
* Safe transaction handling with savepoints
* Automatic exclusion of Many2many and Many2one fields

Key Features:
-------------
* Batch processing (500-1000 records per batch)
* Bulk create for maximum performance
* Progress bar with real-time statistics
* Error handling with skip option
* Detailed migration logs
* Transaction safety with savepoints
* Automatic field type detection
* Performance metrics (records/second)
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/api_migration_views.xml',
        'views/migrator_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {},
    'installable': True,
    'application': True,
    'auto_install': False,
}
