# Data Bridge - Odoo Module

A high-performance data migration tool for Odoo optimized for large datasets (10,000 - 100,000+ records).

## Features

- **Two Migration Methods**: API (from old Odoo ERP) and SQL File upload
- **High Performance**: Batch processing with bulk creates (~500-2000 records/second)
- **Safe**: Transaction handling with savepoints, error recovery
- **Progress Tracking**: Real-time progress bar with statistics
- **Automatic Field Exclusion**: M2M, M2O, O2M, computed fields are automatically skipped

---

## Method 1: API Migration (From Old ERP)

Connect to your old Odoo ERP via XML-RPC and migrate data.

### Usage:
1. Create new API Migration
2. Enter connection details:
   - **URL**: `https://old-erp.example.com`
   - **Database**: `old_database`
   - **Username**: `admin@example.com`
   - **Password**: `****`
3. Click **Test Connection**
4. Enter source/destination models
5. Click **Analyze Fields**
6. Click **Start Migration**

### Optimizations:
- Bulk `create()` instead of individual creates
- Disabled mail tracking during migration
- Savepoint-based error handling
- Connection timeout handling

---

## Method 2: SQL File Migration

Upload a .sql dump file and import data.

### Usage:
1. Create new SQL Migration
2. Upload `.sql` file
3. Enter source table name and destination model
4. Click **Parse SQL File**
5. Click **Start Migration**

### Supported SQL Format:
```sql
INSERT INTO table_name (col1, col2, col3) VALUES 
('val1', 'val2', 'val3'),
('val4', 'val5', 'val6');
```

---

## Performance Benchmarks

| Records | Batch Size | Approx. Time | Records/Second |
|---------|-----------|--------------|----------------|
| 10,000  | 500       | ~10-20s      | ~500-1000      |
| 50,000  | 1000      | ~50-100s     | ~500-1000      |
| 100,000 | 1000      | ~100-200s    | ~500-1000      |

*Performance varies based on field count, data complexity, and server resources.*

---

## Optimizations Applied

### 1. Bulk Create
```python
# Instead of:
for vals in batch:
    Model.create(vals)  # Slow!

# We use:
Model.create(batch_values)  # Fast! Single DB transaction
```

### 2. Disabled Tracking
```python
Model = Model.with_context(
    tracking_disable=True,      # Disable field tracking
    mail_notrack=True,          # Disable mail tracking  
    mail_create_nolog=True,     # No chatter logs
    import_file=True,           # Import mode
)
```

### 3. Savepoint-based Error Handling
```python
try:
    with self.env.cr.savepoint():
        Model.create(batch_values)
except Exception:
    # Fallback to individual creates
    for vals in batch_values:
        with self.env.cr.savepoint():
            Model.create(vals)
```

### 4. Safe Domain Parsing
```python
# Using safe_eval instead of eval
from odoo.tools.safe_eval import safe_eval
domain = safe_eval(domain_filter)
```

---

## Excluded Fields

The following field types are automatically excluded:

| Field Type | Reason |
|------------|--------|
| `many2one` | Requires ID mapping |
| `many2many` | Requires relation handling |
| `one2many` | Inverse relation |
| `binary` | Large data, special handling |
| `reference` | Complex relation |
| Computed fields | Auto-calculated |
| System fields | `id`, `create_date`, `write_date`, etc. |

---

## Configuration Options

### API Migration
- **Batch Size**: Records per batch (default: 500)
- **Limit**: Max records to fetch (0 = all)
- **Offset**: Records to skip
- **Skip Errors**: Continue on failures

### SQL Migration
- **Batch Size**: Records per batch (default: 1000)
- **Clear Destination**: Truncate before import
- **Skip Errors**: Continue on failures

---

## Recommended Settings for Large Datasets

| Dataset Size | Batch Size | Other Settings |
|--------------|-----------|----------------|
| < 10,000 | 500 | Default |
| 10,000 - 50,000 | 1000 | Skip Errors: On |
| 50,000 - 100,000 | 1000 | Skip Errors: On |
| > 100,000 | 1000-2000 | Consider splitting |

---

## Troubleshooting

### Migration is slow
- Increase batch size (try 1000-2000)
- Check server resources (CPU, RAM)
- Ensure destination model doesn't have heavy computed fields

### Connection timeout (API)
- Check network stability
- Try smaller batches
- Verify old ERP server performance

### Memory errors
- Reduce batch size
- Process in smaller chunks using limit/offset

### Records failing
- Check migration logs for error details
- Verify field types match between source/destination
- Enable "Skip Errors" to continue despite failures

---

## Installation

1. Copy `data_bridge` folder to Odoo addons path
2. Restart Odoo server
3. Update Apps List
4. Install "Data Bridge"

---

## License

LGPL-3
