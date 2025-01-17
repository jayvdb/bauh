from bauh.view.qt.view_model import PackageView


def new_pkgs_info() -> dict:
    return {'apps_count': 0,  # number of application packages
            'napps_count': 0,  # number of not application packages (libraries, runtimes or something else)
            'available_types': {},  # available package types in 'new_pkgs'
            'updates': 0,
            'app_updates': 0,
            'napp_updates': 0,
            'pkgs_displayed': [],
            'not_installed': 0,
            'pkgs': []}  # total packages


def update_info(pkgv: PackageView, pkgs_info: dict):
    pkgs_info['available_types'][pkgv.model.get_type()] = pkgv.model.get_type_icon_path()

    if pkgv.model.is_application():
        pkgs_info['apps_count'] += 1
    else:
        pkgs_info['napps_count'] += 1

    if pkgv.model.update:
        if pkgv.model.is_application():
            pkgs_info['app_updates'] += 1
        else:
            pkgs_info['napp_updates'] += 1

        pkgs_info['updates'] += 1

    pkgs_info['pkgs'].append(pkgv)
    pkgs_info['not_installed'] += 1 if not pkgv.model.installed else 0


def apply_filters(pkgv: PackageView, filters: dict, info: dict):
    hidden = filters['only_apps'] and pkgv.model.installed and not pkgv.model.is_application()

    if not hidden and filters['type'] is not None and filters['type'] != 'any':
        hidden = pkgv.model.get_type() != filters['type']

    if not hidden and filters['updates']:
        hidden = not pkgv.model.update

    if not hidden and filters['name']:
        hidden = filters['name'] not in pkgv.model.name.lower()

    if not hidden and (not filters['display_limit'] or len(info['pkgs_displayed']) < filters['display_limit']):
        info['pkgs_displayed'].append(pkgv)
