"""导出指定表的必填字段（NOT NULL 且无默认值），用于构造合法测试数据。

用法：python scripts/inspect_table_columns.py 表1 表2 ...
"""

import sys

import pymysql

TABLES = sys.argv[1:]
if not TABLES:
    raise SystemExit("请传入表名")

conn = pymysql.connect(
    host="127.0.0.1", port=3306, user="root", password="123456", database="erp", charset="utf8mb4"
)
cur = conn.cursor()
for table in TABLES:
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    cols = cur.fetchall()
    required = [
        f"{c[0]}:{c[1].split('(')[0]}"
        for c in cols
        if c[2] == "NO" and c[4] is None and "auto_increment" not in (c[5] or "")
    ]
    fks = []
    cur.execute(
        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA='erp' AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
        (table,),
    )
    for r in cur.fetchall():
        fks.append(f"{r[0]}->{r[1]}.{r[2]}")
    print(f"{table}")
    print(f"   必填: {', '.join(required) if required else '(无)'}")
    if fks:
        print(f"   外键: {', '.join(fks)}")
conn.close()
