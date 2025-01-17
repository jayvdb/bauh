import logging
import platform
import sys

from bauh.api.abstract.cache import MemoryCacheFactory
from bauh.api.abstract.disk import DiskCacheLoaderFactory
from bauh.api.abstract.download import FileDownloader
from bauh.api.http import HttpClient


class ApplicationContext:

    def __init__(self, disk_cache: bool, download_icons: bool, http_client: HttpClient, app_root_dir: str, i18n: dict,
                 cache_factory: MemoryCacheFactory, disk_loader_factory: DiskCacheLoaderFactory,
                 logger: logging.Logger, file_downloader: FileDownloader):
        """
        :param disk_cache: if package data should be cached to disk
        :param download_icons: if packages icons should be downloaded
        :param http_client: a shared instance of http client
        :param app_root_dir: GUI root dir
        :param i18n: the i18n dictionary keys
        :param cache_factory:
        :param disk_loader_factory:
        :param logger: a logger instance
        :param file_downloader:
        """
        self.disk_cache = disk_cache
        self.download_icons = download_icons
        self.http_client = http_client
        self.app_root_dir = app_root_dir
        self.i18n = i18n
        self.cache_factory = cache_factory
        self.disk_loader_factory = disk_loader_factory
        self.logger = logger
        self.file_downloader = file_downloader
        self.arch_x86_64 = sys.maxsize > 2**32

    def is_system_x86_64(self):
        return self.arch_x86_64
