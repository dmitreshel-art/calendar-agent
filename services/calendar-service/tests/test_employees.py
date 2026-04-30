from app.employees import calendar_path_from_mxid, employee_id_from_mxid, parse_mxid


def test_parse_mxid_keeps_federated_homeserver_identity():
    assert parse_mxid('@ivanov:org1.company.ru') == ('ivanov', 'org1.company.ru')
    assert parse_mxid('@ivanov:ORG2.Company.RU') == ('ivanov', 'org2.company.ru')


def test_employee_id_includes_hash_to_avoid_normalization_collisions():
    first = employee_id_from_mxid('@ivanov-1:org1.company.ru')
    second = employee_id_from_mxid('@ivanov_1:org1.company.ru')

    assert first.startswith('org1_company_ru__ivanov_1__')
    assert second.startswith('org1_company_ru__ivanov_1__')
    assert first != second


def test_calendar_path_url_encodes_mxid_parts():
    assert calendar_path_from_mxid('@ivanov:org1.company.ru') == '/calendars/org1.company.ru/ivanov/default'
    assert calendar_path_from_mxid('@ivanov/test:org1.company.ru') == '/calendars/org1.company.ru/ivanov%2Ftest/default'
