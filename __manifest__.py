# -*- encoding: utf-8 -*-
##############################################################################
#
#    Copyright (c) 2024 ZestyBeanz Technologies.
#    (http://wwww.zbeanztech.com)
#    contact@zbeanztech.com
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
{
    'name': 'Data Bridge',
    'version': '19.0.0.0',
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
    'author': 'Vnzn',
    'maintainer': 'VNZN',
    'website': 'https://www.vnzn.in',
    'support': 'vnzntech@gmail.com',
    'license': 'LGPL-3',
    'icon': "/data_bridge/static/description/icon.png",
    'images': ['static/description/banners/banner.png',],
    'currency': 'USD',
    'price': 99.0,
    'depends': ['base', 'mail', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/api_migration_views.xml',
        'views/migrator_views.xml',
        'views/menu_views.xml',
        'wizard/o2m_migration_wizard_view.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'data_bridge/static/src/js/reload.js',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
