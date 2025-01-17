import datetime
from typing import List

from bauh.api.abstract.model import SoftwarePackage
from bauh.api.constants import CACHE_PATH
from bauh.commons import resource

from bauh.gems.arch import ROOT_DIR

CACHED_ATTRS = {'command', 'icon_path', 'mirror', 'maintainer', 'desktop_entry'}


class ArchPackage(SoftwarePackage):

    def __init__(self, name: str = None, version: str = None, latest_version: str = None, description: str = None,
                 package_base: str = None, votes: int = None, popularity: float = None,
                 first_submitted: datetime.datetime = None, last_modified: datetime.datetime = None,
                 maintainer: str = None, url_download: str = None, pkgbuild: str = None, mirror: str = None,
                 desktop_entry: str = None, installed: bool = False):

        super(ArchPackage, self).__init__(name=name, version=version, latest_version=latest_version, description=description, installed=installed)
        self.package_base = package_base
        self.votes = votes
        self.popularity = popularity
        self.maintainer = maintainer
        self.url_download = url_download
        self.first_submitted = first_submitted
        self.last_modified = last_modified
        self.pkgbuild = pkgbuild
        self.mirror = mirror
        self.command = None
        self.icon_path = None
        self.downgrade_enabled = False
        self.desktop_entry = desktop_entry

    @staticmethod
    def disk_cache_path(pkgname: str, mirror: str):
        return CACHE_PATH + '/arch/installed/' + ('aur' if mirror == 'aur' else 'mirror') + '/' + pkgname

    def get_pkg_build_url(self):
        if self.package_base:
            return 'https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=' + self.package_base

    def has_history(self):
        return self.installed

    def has_info(self):
        return True

    def can_be_installed(self):
        return super(ArchPackage, self).can_be_installed() and self.url_download

    def can_be_downgraded(self):
        return self.installed and self.downgrade_enabled

    def get_type(self):
        return 'aur' if self.mirror == 'aur' else 'arch'

    def get_default_icon_path(self) -> str:
        return self.get_type_icon_path()

    def get_disk_icon_path(self) -> str:
        return self.icon_path

    def get_type_icon_path(self):
        return resource.get_path('img/arch.png', ROOT_DIR)  # TODO change icon when from mirrors

    def is_application(self):
        return self.can_be_run()

    def supports_disk_cache(self):
        return True

    def get_disk_cache_path(self) -> str:
        if self.name:
            return self.disk_cache_path(self.name, self.mirror)

    def get_data_to_cache(self) -> dict:
        cache = {}

        # required attrs to cache
        for a in CACHED_ATTRS:
            val = getattr(self, a)

            if val:
                cache[a] = val

        return cache

    def fill_cached_data(self, data: dict):
        if data:
            for a in CACHED_ATTRS:
                val = data.get(a)
                if val:
                    setattr(self, a, val)

                    if a == 'icon_path':
                        self.icon_url = val

    def can_be_run(self) -> bool:
        # only returns if there is a desktop entry set for the application to avoid running command-line applications
        return bool(self.desktop_entry) and bool(self.command)

    def get_publisher(self):
        return self.maintainer

    def set_icon(self, paths: List[str]):
        self.icon_path = paths[0]

        if len(paths) > 1:
            for path in paths:
                if '/' in path:
                    self.icon_path = path
                    break

        self.icon_url = self.icon_path

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return '{} (name={}, command={}, icon_path={})'.format(self.__class__.__name__, self.name, self.command, self.icon_path)
