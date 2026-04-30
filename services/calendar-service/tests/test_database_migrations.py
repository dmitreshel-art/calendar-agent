import sqlite3

from app.database import ensure_sqlite_schema_columns


def test_ensure_sqlite_schema_columns_adds_employee_role(tmp_path):
    db_path = tmp_path / 'app.db'
    con = sqlite3.connect(db_path)
    con.execute('create table employees (id text primary key, matrix_id text)')
    con.commit()
    con.close()

    ensure_sqlite_schema_columns(f'sqlite:///{db_path}')

    con = sqlite3.connect(db_path)
    cols = [row[1] for row in con.execute('pragma table_info(employees)').fetchall()]
    default_row = con.execute("select dflt_value from pragma_table_info('employees') where name='role'").fetchone()
    con.close()

    assert 'role' in cols
    assert default_row[0] == "'user'"
