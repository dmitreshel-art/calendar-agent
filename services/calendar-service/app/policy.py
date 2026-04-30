from .models import Employee, EmployeeRole, Meeting


def is_calendar_admin(employee: Employee) -> bool:
    return employee.role == EmployeeRole.calendar_admin.value


def can_view_details(requester: Employee, target: Employee) -> bool:
    return requester.id == target.id


def can_modify_meeting(requester: Employee, meeting: Meeting) -> bool:
    return requester.id == meeting.organizer_employee_id


def can_clear_own_calendar(requester: Employee, target: Employee) -> bool:
    return requester.id == target.id


def can_admin_clear_calendar(admin: Employee, target: Employee) -> bool:
    return is_calendar_admin(admin)
