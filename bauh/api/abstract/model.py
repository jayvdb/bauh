from abc import ABC, abstractmethod
from enum import Enum
from typing import List

from bauh.api.constants import CACHE_PATH


class PackageAction:

    def __init__(self, i18_label_key: str, i18n_status_key: str, icon_path: str, manager_method: str, requires_root: bool):
        """
        :param i18_label_key: the i18n key that will be used to display the action name
        :param i18n_status_key: the i18n key that will be used to display the action name being executed
        :param icon_path: the action icon path. Use None for no icon
        :param manager_method: the SoftwareManager method name that should be called. The method must has the following parameters: (pkg: SoftwarePackage, root_password: str, watcher: ProcessWatcher)
        :param requires_root:
        """
        self.id = id
        self.i18_label_key = i18_label_key
        self.i18n_status_key = i18n_status_key
        self.icon_path = icon_path
        self.manager_method = manager_method
        self.requires_root = requires_root


class PackageStatus(Enum):
    READY = 1  # when all package data is already filled
    LOADING_DATA = 2  # when some package data is already being retrieved asynchronously


class SoftwarePackage(ABC):

    def __init__(self, id: str = None, version: str = None, name: str = None, description: str = None, latest_version: str = None,
                 icon_url: str = None, status: PackageStatus = PackageStatus.READY, installed: bool = False, update: bool = False,
                 size: int = None, categories: List[str] = None):
        """
        :param id:
        :param version:
        :param name:
        :param description:
        :param latest_version:
        :param icon_url: the path to the package icon. It can be an URL or PATH
        :param status:
        :param installed:
        :param update: if there is an update for package
        :param size: package size in BYTES
        :param categories: package categories. i.e: video editor, web browser, etc
        """
        self.id = id
        self.name = name
        self.description = description
        self.status = status
        self.version = version
        self.latest_version = latest_version
        self.icon_url = icon_url
        self.installed = installed
        self.update = update
        self.size = size
        self.categories = categories

    @abstractmethod
    def has_history(self):
        """
        :return: if the application has a commit history that can be shown to the user
        """
        pass

    @abstractmethod
    def has_info(self):
        """
        :return: if the application has additional information that can be shown to the user
        """
        pass

    @abstractmethod
    def can_be_downgraded(self):
        pass

    def can_be_uninstalled(self):
        return self.installed

    def can_be_installed(self):
        return not self.installed

    @abstractmethod
    def get_type(self):
        """
        :return: a string that represents the application type
        """
        pass

    @abstractmethod
    def get_default_icon_path(self):
        """
        :return: the path of a default icon when the application icon could not be retrieved (or will not be retrieved)
        """
        pass

    @abstractmethod
    def get_type_icon_path(self):
        """
            :return: the path of the application type icon
        """

    @abstractmethod
    def is_application(self):
        """
        :return: if the package is an application
        """
        pass

    def supports_disk_cache(self):
        """
        :return: if some application data and icon should be cached to the user disk
        """
        return self.installed and self.is_application()

    def get_disk_cache_path(self):
        """
        :return: base cache path for the specific app type
        """
        return CACHE_PATH + '/' + self.get_type()

    def get_disk_icon_path(self):
        return '{}/icon.png'.format(self.get_disk_cache_path())

    def get_disk_data_path(self):
        return '{}/data.json'.format(self.get_disk_cache_path())

    @abstractmethod
    def get_data_to_cache(self) -> dict:
        """
        :return: the application data that should be cached in disk / memory for quick access
        """
        pass

    @abstractmethod
    def fill_cached_data(self, data: dict):
        """
        sets cached data to the current instance
        :param data:
        :return:
        """
        pass

    @abstractmethod
    def can_be_run(self) -> str:
        """
        :return: whether the app can be run via the GUI
        """

    def is_trustable(self) -> bool:
        """
        :return: if the package is distributed by a trustable source
        """
        return False

    @abstractmethod
    def get_publisher(self) -> str:
        """
        :return: the package publisher / maintainer
        """
        pass

    def get_custom_supported_actions(self) -> List[PackageAction]:
        """
        :return: custom supported actions
        """
        pass

    def __str__(self):
        return '{} (id={}, name={})'.format(self.__class__.__name__, self.id, self.name)


class PackageUpdate:

    def __init__(self, pkg_id: str, version: str, pkg_type: str):
        """
        :param pkg_id: an unique package identifier
        :param version: the new version
        :param pkg_type: the package type
        """
        self.id = pkg_id
        self.version = version
        self.type = pkg_type

    def __str__(self):
        return '{} (id={}, type={}, new_version={})'.format(self.__class__.__name__, self.id, self.type, self.type)


class PackageHistory:

    def __init__(self, pkg: SoftwarePackage, history: List[dict], pkg_status_idx: int):
        """
        :param pkg
        :param history: a list with the package history.
        :param pkg_status_idx: 'history' index in which the application is current found
        """
        self.pkg = pkg
        self.history = history
        self.pkg_status_idx = pkg_status_idx


class SuggestionPriority(Enum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    TOP = 3


class PackageSuggestion:

    def __init__(self, package: SoftwarePackage, priority: SuggestionPriority):
        self.package = package
        self.priority = priority
