import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from threading import Thread
from typing import List, Set, Type

import requests

from bauh.api.abstract.controller import SearchResult, SoftwareManager, ApplicationContext
from bauh.api.abstract.disk import DiskCacheLoader
from bauh.api.abstract.handler import ProcessWatcher
from bauh.api.abstract.model import PackageUpdate, PackageHistory, SoftwarePackage, PackageSuggestion, PackageStatus
from bauh.api.abstract.view import MessageType
from bauh.commons.html import bold
from bauh.commons.system import SystemProcess, ProcessHandler, new_subprocess, run_cmd, new_root_subprocess, \
    SimpleProcess
from bauh.gems.arch import BUILD_DIR, aur, pacman, makepkg, pkgbuild, message, confirmation, disk, git, suggestions, gpg
from bauh.gems.arch.aur import AURClient
from bauh.gems.arch.mapper import ArchDataMapper
from bauh.gems.arch.model import ArchPackage
from bauh.gems.arch.worker import AURIndexUpdater, ArchDiskCacheUpdater, ArchCompilationOptimizer

URL_GIT = 'https://aur.archlinux.org/{}.git'
URL_PKG_DOWNLOAD = 'https://aur.archlinux.org/cgit/aur.git/snapshot/{}.tar.gz'
URL_SRC_INFO = 'https://aur.archlinux.org/cgit/aur.git/plain/.SRCINFO?h='

RE_SPLIT_VERSION = re.compile(r'(=|>|<)')

SOURCE_FIELDS = ('source', 'source_x86_64')
RE_PRE_DOWNLOADABLE_FILES = re.compile(r'(https?|ftp)://.+\.\w+[^gpg|git]$')


class ArchManager(SoftwareManager):

    def __init__(self, context: ApplicationContext):
        super(ArchManager, self).__init__(context=context)
        self.aur_cache = context.cache_factory.new()
        # context.disk_loader_factory.map(ArchPackage, self.aur_cache) TODO

        self.mapper = ArchDataMapper(http_client=context.http_client)
        self.i18n = context.i18n
        self.aur_client = AURClient(context.http_client)
        self.names_index = {}
        self.aur_index_updater = AURIndexUpdater(context, self)
        self.dcache_updater = ArchDiskCacheUpdater(context.logger, context.disk_cache)
        self.comp_optimizer = ArchCompilationOptimizer(context.logger)
        self.logger = context.logger
        self.enabled = True
        self.arch_distro = os.path.exists('/etc/arch-release')

    def _upgrade_search_result(self, apidata: dict, installed_pkgs: dict, downgrade_enabled: bool, res: SearchResult, disk_loader: DiskCacheLoader):
        app = self.mapper.map_api_data(apidata, installed_pkgs['not_signed'])
        app.downgrade_enabled = downgrade_enabled

        if app.installed:
            res.installed.append(app)

            if disk_loader:
                disk_loader.fill(app)
        else:
            res.new.append(app)

        Thread(target=self.mapper.fill_package_build, args=(app,)).start()

    def search(self, words: str, disk_loader: DiskCacheLoader, limit: int = -1) -> SearchResult:
        self.comp_optimizer.join()

        downgrade_enabled = git.is_enabled()
        res = SearchResult([], [], 0)

        installed = {}
        read_installed = Thread(target=lambda: installed.update(pacman.list_and_map_installed()))
        read_installed.start()

        api_res = self.aur_client.search(words)

        if api_res and api_res.get('results'):
            read_installed.join()

            for pkgdata in api_res['results']:
                self._upgrade_search_result(pkgdata, installed, downgrade_enabled, res, disk_loader)

        else:  # if there are no results from the API (it could be because there were too many), tries the names index:
            if self.names_index:

                to_query = set()
                for norm_name, real_name in self.names_index.items():
                    if words in norm_name:
                        to_query.add(real_name)

                    if len(to_query) == 25:
                        break

                pkgsinfo = self.aur_client.get_info(to_query)

                if pkgsinfo:
                    read_installed.join()

                    for pkgdata in pkgsinfo:
                        self._upgrade_search_result(pkgdata, installed, res)

        res.total = len(res.installed) + len(res.new)
        return res

    def _fill_aur_pkgs(self, not_signed: dict, pkgs: list, disk_loader: DiskCacheLoader, internet_available: bool):
        downgrade_enabled = git.is_enabled()

        if internet_available:
            try:
                pkgsinfo = self.aur_client.get_info(not_signed.keys())

                if pkgsinfo:
                    for pkgdata in pkgsinfo:
                        pkg = self.mapper.map_api_data(pkgdata, not_signed)
                        pkg.downgrade_enabled = downgrade_enabled

                        if disk_loader:
                            disk_loader.fill(pkg)
                            pkg.status = PackageStatus.READY

                        pkgs.append(pkg)

                return
            except requests.exceptions.ConnectionError:
                self.logger.warning('Could not retrieve installed AUR packages API data. It seems the internet connection is off.')
                self.logger.info("Reading only local AUR packages data")

        for name, data in not_signed.items():
            pkg = ArchPackage(name=name, version=data.get('version'),
                              latest_version=data.get('version'), description=data.get('description'),
                              installed=True, mirror='aur')
            pkg.downgrade_enabled = downgrade_enabled

            if disk_loader:
                disk_loader.fill(pkg)
                pkg.status = PackageStatus.READY

            pkgs.append(pkg)

    def _fill_mirror_pkgs(self, mirrors: dict, apps: list):
        # TODO
        for name, data in mirrors.items():
            app = ArchPackage(name=name, version=data.get('version'), latest_version=data.get('version'), description=data.get('description'))
            app.installed = True
            app.mirror = ''  # TODO
            app.update = False  # TODO
            apps.append(app)

    def read_installed(self, disk_loader: DiskCacheLoader, limit: int = -1, only_apps: bool = False, pkg_types: Set[Type[SoftwarePackage]] = None, internet_available: bool = None) -> SearchResult:
        installed = pacman.list_and_map_installed()

        apps = []
        if installed and installed['not_signed']:
            self.dcache_updater.join()

            self._fill_aur_pkgs(installed['not_signed'], apps, disk_loader, internet_available)

        return SearchResult(apps, None, len(apps))

    def downgrade(self, pkg: ArchPackage, root_password: str, watcher: ProcessWatcher) -> bool:

        handler = ProcessHandler(watcher)
        app_build_dir = '{}/build_{}'.format(BUILD_DIR, int(time.time()))
        watcher.change_progress(5)

        try:
            if not os.path.exists(app_build_dir):
                build_dir = handler.handle(SystemProcess(new_subprocess(['mkdir', '-p', app_build_dir])))

                if build_dir:
                    watcher.change_progress(10)
                    watcher.change_substatus(self.i18n['arch.clone'].format(bold(pkg.name)))
                    clone = handler.handle(SystemProcess(subproc=new_subprocess(['git', 'clone', URL_GIT.format(pkg.name)], cwd=app_build_dir), check_error_output=False))
                    watcher.change_progress(30)
                    if clone:
                        watcher.change_substatus(self.i18n['arch.downgrade.reading_commits'])
                        clone_path = '{}/{}'.format(app_build_dir, pkg.name)
                        pkgbuild_path = '{}/PKGBUILD'.format(clone_path)

                        commits = run_cmd("git log", cwd=clone_path)
                        watcher.change_progress(40)

                        if commits:
                            commit_list = re.findall(r'commit (.+)\n', commits)
                            if commit_list:
                                if len(commit_list) > 1:
                                    for idx in range(1, len(commit_list)):
                                        commit = commit_list[idx]
                                        with open(pkgbuild_path) as f:
                                            pkgdict = aur.map_pkgbuild(f.read())

                                        if not handler.handle(SystemProcess(subproc=new_subprocess(['git', 'reset', '--hard', commit], cwd=clone_path), check_error_output=False)):
                                            watcher.print('Could not downgrade anymore. Aborting...')
                                            return False

                                        if '{}-{}'.format(pkgdict.get('pkgver'), pkgdict.get('pkgrel')) == pkg.version:
                                            # current version found
                                            watcher.change_substatus(self.i18n['arch.downgrade.version_found'])
                                            break

                                    watcher.change_substatus(self.i18n['arch.downgrade.install_older'])
                                    return self._make_pkg(pkg.name, pkg.maintainer, root_password, handler, app_build_dir, clone_path, dependency=False, skip_optdeps=True)
                                else:
                                    watcher.show_message(title=self.i18n['arch.downgrade.error'],
                                                         body=self.i18n['arch.downgrade.impossible'].format(pkg.name),
                                                         type_=MessageType.ERROR)
                                    return False

                        watcher.show_message(title=self.i18n['error'], body=self.i18n['arch.downgrade.no_commits'], type_=MessageType.ERROR)
                        return False

        finally:
            if os.path.exists(app_build_dir):
                handler.handle(SystemProcess(subproc=new_subprocess(['rm', '-rf', app_build_dir])))

        return False

    def clean_cache_for(self, pkg: ArchPackage):
        if os.path.exists(pkg.get_disk_cache_path()):
            shutil.rmtree(pkg.get_disk_cache_path())

    def update(self, pkg: ArchPackage, root_password: str, watcher: ProcessWatcher) -> bool:
        return self.install(pkg=pkg, root_password=root_password, watcher=watcher, skip_optdeps=True)

    def _uninstall(self, pkg_name: str, root_password: str, handler: ProcessHandler) -> bool:
        res = handler.handle(SystemProcess(new_root_subprocess(['pacman', '-R', pkg_name, '--noconfirm'], root_password)))

        if res:
            cached_paths = [ArchPackage.disk_cache_path(pkg_name, 'aur'), ArchPackage.disk_cache_path(pkg_name, 'mirror')]

            for path in cached_paths:
                if os.path.exists(path):
                    shutil.rmtree(path)
                    break

        return res

    def uninstall(self, pkg: ArchPackage, root_password: str, watcher: ProcessWatcher) -> bool:
        handler = ProcessHandler(watcher)

        watcher.change_progress(10)
        info = pacman.get_info_dict(pkg.name)
        watcher.change_progress(50)

        if info.get('required by'):
            pkname = bold(pkg.name)
            msg = '{}:<br/><br/>{}<br/><br/>{}'.format(self.i18n['arch.uninstall.required_by'].format(pkname), bold(info['required by']), self.i18n['arch.uninstall.required_by.advice'].format(pkname))
            watcher.show_message(title=self.i18n['error'], body=msg, type_=MessageType.WARNING)
            return False

        uninstalled = self._uninstall(pkg.name, root_password, handler)
        watcher.change_progress(100)
        return uninstalled

    def get_managed_types(self) -> Set["type"]:
        return {ArchPackage}

    def get_info(self, pkg: ArchPackage) -> dict:
        if pkg.installed:
            t = Thread(target=self.mapper.fill_package_build, args=(pkg,))
            t.start()

            info = pacman.get_info_dict(pkg.name)

            t.join()

            if pkg.pkgbuild:
                info['13_pkg_build'] = pkg.pkgbuild

            info['14_installed_files'] = pacman.list_installed_files(pkg.name)

            return info
        else:
            info = {
                '01_id': pkg.id,
                '02_name': pkg.name,
                '03_version': pkg.version,
                '04_popularity': pkg.popularity,
                '05_votes': pkg.votes,
                '06_package_base': pkg.package_base,
                '07_maintainer': pkg.maintainer,
                '08_first_submitted': pkg.first_submitted,
                '09_last_modified': pkg.last_modified,
                '10_url': pkg.url_download
            }

            srcinfo = self.aur_client.get_src_info(pkg.name)

            if srcinfo:
                if srcinfo.get('depends'):
                    info['11_dependson'] = srcinfo['depends']

                if srcinfo.get('optdepends'):
                    info['12_optdepends'] = srcinfo['optdepends']

            if pkg.pkgbuild:
                info['00_pkg_build'] = pkg.pkgbuild
            else:
                info['11_pkg_build_url'] = pkg.get_pkg_build_url()

            return info

    def get_history(self, pkg: ArchPackage) -> PackageHistory:
        temp_dir = '{}/build_{}'.format(BUILD_DIR, int(time.time()))

        try:
            Path(temp_dir).mkdir(parents=True)
            run_cmd('git clone ' + URL_GIT.format(pkg.name), print_error=False, cwd=temp_dir)

            clone_path = '{}/{}'.format(temp_dir, pkg.name)
            pkgbuild_path = '{}/PKGBUILD'.format(clone_path)

            commits = git.list_commits(clone_path)

            if commits:
                history, status_idx = [], -1

                for idx, commit in enumerate(commits):
                    with open(pkgbuild_path) as f:
                        pkgdict = aur.map_pkgbuild(f.read())

                    if status_idx < 0 and '{}-{}'.format(pkgdict.get('pkgver'), pkgdict.get('pkgrel')) == pkg.version:
                        status_idx = idx

                    history.append({'1_version': pkgdict['pkgver'], '2_release': pkgdict['pkgrel'],
                                    '3_date': commit['date']})  # the number prefix is to ensure the rendering order

                    if idx + 1 < len(commits):
                        if not run_cmd('git reset --hard ' + commits[idx + 1]['commit'], cwd=clone_path):
                            break

                return PackageHistory(pkg=pkg, history=history, pkg_status_idx=status_idx)
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def _install_deps(self, deps: Set[str], pkg_mirrors: dict, root_password: str, handler: ProcessHandler, change_progress: bool = False) -> str:
        """
        :param deps:
        :param pkg_mirrors:
        :param root_password:
        :param handler:
        :return: not installed dependency
        """
        progress_increment = int(100 / len(deps))
        progress = 0
        self._update_progress(handler.watcher, 1, change_progress)

        for pkgname in deps:

            mirror = pkg_mirrors[pkgname]
            handler.watcher.change_substatus(self.i18n['arch.install.dependency.install'].format(bold('{} ()'.format(pkgname, mirror))))
            if mirror == 'aur':
                installed = self._install_from_aur(pkgname=pkgname, maintainer=None, root_password=root_password, handler=handler, dependency=True, change_progress=False)
            else:
                installed = self._install(pkgname=pkgname, maintainer=None, root_password=root_password, handler=handler, install_file=None, mirror=mirror, change_progress=False)

            if not installed:
                return pkgname

            progress += progress_increment
            self._update_progress(handler.watcher, progress, change_progress)

        self._update_progress(handler.watcher, 100, change_progress)

    def _map_mirrors(self, pkgnames: Set[str]) -> dict:
        pkg_mirrors = pacman.get_mirrors(pkgnames)  # getting mirrors set

        if len(pkgnames) != pkg_mirrors:  # checking if any dep not found in the distro mirrors are from AUR
            nomirrors = {p for p in pkgnames if p not in pkg_mirrors}
            for pkginfo in self.aur_client.get_info(nomirrors):
                if pkginfo.get('Name') in nomirrors:
                    pkg_mirrors[pkginfo['Name']] = 'aur'

        return pkg_mirrors

    def _pre_download_source(self, pkgname: str, project_dir: str, watcher: ProcessWatcher) -> bool:
        if self.context.file_downloader.is_multithreaded():
            srcinfo = self.aur_client.get_src_info(pkgname)

            pre_download_files = []

            for attr in SOURCE_FIELDS:
                if srcinfo.get(attr):
                    if attr == 'source_x86_x64' and not self.context.is_system_x86_64():
                        continue
                    else:
                        for f in srcinfo[attr]:
                            if RE_PRE_DOWNLOADABLE_FILES.findall(f):
                                pre_download_files.append(f)

            if pre_download_files:
                downloader = self.context.file_downloader.get_default_client_name()

                for f in pre_download_files:
                    fdata = f.split('::')

                    args = {'watcher': watcher, 'cwd': project_dir}
                    if len(fdata) > 1:
                        args.update({'file_url': fdata[1], 'output_path': fdata[0]})
                    else:
                        args.update({'file_url': fdata[0], 'output_path': None})

                    file_size = self.context.http_client.get_content_length(args['file_url'])
                    file_size = int(file_size) / (1024 ** 2) if file_size else None

                    watcher.change_substatus(bold('[{}] ').format(downloader) + self.i18n['downloading'] + ' ' + bold(args['file_url'].split('/')[-1]) + ' ( {0:.2f} Mb )'.format(file_size) if file_size else '')
                    if not self.context.file_downloader.download(**args):
                        watcher.print('Could not download source file {}'.format(args['file_url']))
                        return False

        return True

    def _make_pkg(self, pkgname: str, maintainer: str, root_password: str, handler: ProcessHandler, build_dir: str, project_dir: str, dependency: bool, skip_optdeps: bool = False, change_progress: bool = True) -> bool:

        self._pre_download_source(pkgname, project_dir, handler.watcher)

        self._update_progress(handler.watcher, 50, change_progress)
        if not self._install_missings_deps_and_keys(pkgname, root_password, handler, project_dir):
            return False

        # building main package
        handler.watcher.change_substatus(self.i18n['arch.building.package'].format(bold(pkgname)))
        pkgbuilt, output = handler.handle_simple(SimpleProcess(['makepkg', '-ALcsmf'], cwd=project_dir))
        self._update_progress(handler.watcher, 65, change_progress)

        if pkgbuilt:
            gen_file = [fname for root, dirs, files in os.walk(build_dir) for fname in files if re.match(r'^{}-.+\.tar\.xz'.format(pkgname), fname)]

            if not gen_file:
                handler.watcher.print('Could not find generated .tar.xz file. Aborting...')
                return False

            install_file = '{}/{}'.format(project_dir, gen_file[0])

            if self._install(pkgname=pkgname, maintainer=maintainer, root_password=root_password, mirror='aur', handler=handler,
                             install_file=install_file, pkgdir=project_dir, change_progress=change_progress):

                if dependency or skip_optdeps:
                    return True

                handler.watcher.change_substatus(self.i18n['arch.optdeps.checking'].format(bold(pkgname)))

                if self._install_optdeps(pkgname, root_password, handler, project_dir, change_progress=change_progress):
                    return True

        return False

    def _install_missings_deps_and_keys(self, pkgname: str, root_password: str, handler: ProcessHandler, pkgdir: str) -> bool:
        handler.watcher.change_substatus(self.i18n['arch.checking.deps'].format(bold(pkgname)))
        check_res = makepkg.check(pkgdir, handler)

        if check_res:
            if check_res.get('missing_deps'):
                depnames = {RE_SPLIT_VERSION.split(dep)[0] for dep in check_res['missing_deps']}
                dep_mirrors = self._map_mirrors(depnames)

                for dep in depnames:  # cheking if a dependency could not be found in any mirror
                    if dep not in dep_mirrors:
                        message.show_dep_not_found(dep, self.i18n, handler.watcher)
                        return False

                handler.watcher.change_substatus(self.i18n['arch.missing_deps_found'].format(bold(pkgname)))

                if not confirmation.request_install_missing_deps(pkgname, dep_mirrors, handler.watcher, self.i18n):
                    handler.watcher.print(self.i18n['action.cancelled'])
                    return False

                dep_not_installed = self._install_deps(depnames, dep_mirrors, root_password, handler, change_progress=False)

                if dep_not_installed:
                    message.show_dep_not_installed(handler.watcher, pkgname, dep_not_installed, self.i18n)
                    return False

                # it is necessary to re-check because missing PGP keys are only notified when there are none missing
                return self._install_missings_deps_and_keys(pkgname, root_password, handler, pkgdir)

            if check_res.get('gpg_key'):
                if handler.watcher.request_confirmation(title=self.i18n['arch.aur.install.unknown_key.title'],
                                                        body=self.i18n['arch.install.aur.unknown_key.body'].format(bold(pkgname), bold(check_res['gpg_key']))):
                    handler.watcher.change_substatus(self.i18n['arch.aur.install.unknown_key.status'].format(bold(check_res['gpg_key'])))
                    if not handler.handle(gpg.receive_key(check_res['gpg_key'])):
                        handler.watcher.show_message(title=self.i18n['error'],
                                                     body=self.i18n['arch.aur.install.unknown_key.receive_error'].format(bold(check_res['gpg_key'])))
                        return False
                else:
                    handler.watcher.print(self.i18n['action.cancelled'])
                    return False

        return True

    def _install_optdeps(self, pkgname: str, root_password: str, handler: ProcessHandler, pkgdir: str, change_progress: bool = True) -> bool:
        with open('{}/.SRCINFO'.format(pkgdir)) as f:
            odeps = pkgbuild.read_optdeps_as_dict(f.read())

        if not odeps:
            return True

        to_install = {d for d in odeps if not pacman.check_installed(d)}

        if not to_install:
            return True

        pkg_mirrors = self._map_mirrors(to_install)

        if pkg_mirrors:
            final_optdeps = {dep: {'desc': odeps.get(dep), 'mirror': pkg_mirrors.get(dep)} for dep in to_install if dep in pkg_mirrors}

            deps_to_install = confirmation.request_optional_deps(pkgname, final_optdeps, handler.watcher, self.i18n)

            if not deps_to_install:
                return True
            else:
                dep_not_installed = self._install_deps(deps_to_install, pkg_mirrors, root_password, handler, change_progress=True)

                if dep_not_installed:
                    message.show_optdep_not_installed(dep_not_installed, handler.watcher, self.i18n)
                    return False

        return True

    def _install(self, pkgname: str, maintainer: str, root_password: str, mirror: str, handler: ProcessHandler, install_file: str = None, pkgdir: str = '.', change_progress: bool = True):
        check_install_output = []
        pkgpath = install_file if install_file else pkgname

        handler.watcher.change_substatus(self.i18n['arch.checking.conflicts'].format(bold(pkgname)))

        for check_out in SimpleProcess(['pacman', '-U' if install_file else '-S', pkgpath], root_password=root_password, cwd=pkgdir).instance.stdout:
            check_install_output.append(check_out.decode())

        self._update_progress(handler.watcher, 70, change_progress)
        if check_install_output and 'conflict' in check_install_output[-1]:
            conflicting_apps = [w[0] for w in re.findall(r'((\w|\-|\.)+)\s(and|are)', check_install_output[-1])]
            conflict_msg = ' {} '.format(self.i18n['and']).join([bold(c) for c in conflicting_apps])
            if not handler.watcher.request_confirmation(title=self.i18n['arch.install.conflict.popup.title'],
                                                        body=self.i18n['arch.install.conflict.popup.body'].format(conflict_msg)):
                handler.watcher.print(self.i18n['action.cancelled'])
                return False
            else:  # uninstall conflicts
                self._update_progress(handler.watcher, 75, change_progress)
                to_uninstall = [conflict for conflict in conflicting_apps if conflict != pkgname]

                for conflict in to_uninstall:
                    handler.watcher.change_substatus(self.i18n['arch.uninstalling.conflict'].format(bold(conflict)))
                    if not self._uninstall(conflict, root_password, handler):
                        handler.watcher.show_message(title=self.i18n['error'],
                                                     body=self.i18n['arch.uninstalling.conflict.fail'].format(bold(conflict)),
                                                     type_=MessageType.ERROR)
                        return False

        handler.watcher.change_substatus(self.i18n['arch.installing.package'].format(bold(pkgname)))
        self._update_progress(handler.watcher, 80, change_progress)
        installed = handler.handle(pacman.install_as_process(pkgpath=pkgpath, root_password=root_password, aur=install_file is not None, pkgdir=pkgdir))
        self._update_progress(handler.watcher, 95, change_progress)

        if installed and self.context.disk_cache:
            handler.watcher.change_substatus(self.i18n['status.caching_data'].format(bold(pkgname)))
            if self.context.disk_cache:
                disk.save_several({pkgname}, mirror=mirror, maintainer=maintainer, overwrite=True)

            self._update_progress(handler.watcher, 100, change_progress)

        return installed

    def _update_progress(self, watcher: ProcessWatcher, val: int, change_progress: bool):
        if change_progress:
            watcher.change_progress(val)

    def _import_pgp_keys(self, pkgname: str, root_password: str, handler: ProcessHandler):
        srcinfo = self.aur_client.get_src_info(pkgname)

        if srcinfo.get('validpgpkeys'):
            handler.watcher.print(self.i18n['arch.aur.install.verifying_pgp'])
            keys_to_download = [key for key in srcinfo['validpgpkeys'] if not pacman.verify_pgp_key(key)]

            if keys_to_download:
                keys_str = ''.join(
                    ['<br/><span style="font-weight:bold">  - {}</span>'.format(k) for k in keys_to_download])
                msg_body = '{}:<br/>{}<br/><br/>{}'.format(self.i18n['arch.aur.install.pgp.body'].format(bold(pkgname)),
                                                           keys_str, self.i18n['ask.continue'])

                if handler.watcher.request_confirmation(title=self.i18n['arch.aur.install.pgp.title'], body=msg_body):
                    for key in keys_to_download:
                        handler.watcher.change_substatus(self.i18n['arch.aur.install.pgp.substatus'].format(bold(key)))
                        if not handler.handle(pacman.receive_key(key, root_password)):
                            handler.watcher.show_message(title=self.i18n['error'],
                                                         body=self.i18n['arch.aur.install.pgp.receive_fail'].format(
                                                             bold(key)),
                                                         type_=MessageType.ERROR)
                            return False

                        if not handler.handle(pacman.sign_key(key, root_password)):
                            handler.watcher.show_message(title=self.i18n['error'],
                                                         body=self.i18n['arch.aur.install.pgp.sign_fail'].format(
                                                             bold(key)),
                                                         type_=MessageType.ERROR)
                            return False

                        handler.watcher.change_substatus(self.i18n['arch.aur.install.pgp.success'])
                else:
                    handler.watcher.print(self.i18n['action.cancelled'])
                    return False

    def _install_from_aur(self, pkgname: str, maintainer: str, root_password: str, handler: ProcessHandler, dependency: bool, skip_optdeps: bool = False, change_progress: bool = True) -> bool:
        app_build_dir = '{}/build_{}'.format(BUILD_DIR, int(time.time()))

        try:
            if not os.path.exists(app_build_dir):
                build_dir = handler.handle(SystemProcess(new_subprocess(['mkdir', '-p', app_build_dir])))
                self._update_progress(handler.watcher, 10, change_progress)

                if build_dir:
                    file_url = URL_PKG_DOWNLOAD.format(pkgname)
                    file_name = file_url.split('/')[-1]
                    handler.watcher.change_substatus('{} {}'.format(self.i18n['arch.downloading.package'], bold(file_name)))
                    download = handler.handle(SystemProcess(new_subprocess(['wget', file_url], cwd=app_build_dir), check_error_output=False))

                    if download:
                        self._update_progress(handler.watcher, 30, change_progress)
                        handler.watcher.change_substatus('{} {}'.format(self.i18n['arch.uncompressing.package'], bold(file_name)))
                        uncompress = handler.handle(SystemProcess(new_subprocess(['tar', 'xvzf', '{}.tar.gz'.format(pkgname)], cwd=app_build_dir)))
                        self._update_progress(handler.watcher, 40, change_progress)

                        if uncompress:
                            uncompress_dir = '{}/{}'.format(app_build_dir, pkgname)
                            return self._make_pkg(pkgname=pkgname,
                                                  maintainer=maintainer,
                                                  root_password=root_password,
                                                  handler=handler,
                                                  build_dir=app_build_dir,
                                                  project_dir=uncompress_dir,
                                                  dependency=dependency,
                                                  skip_optdeps=skip_optdeps,
                                                  change_progress=change_progress)
        finally:
            if os.path.exists(app_build_dir):
                handler.handle(SystemProcess(new_subprocess(['rm', '-rf', app_build_dir])))

        return False

    def install(self, pkg: ArchPackage, root_password: str, watcher: ProcessWatcher, skip_optdeps: bool = False) -> bool:
        res = self._install_from_aur(pkg.name, pkg.maintainer, root_password, ProcessHandler(watcher), dependency=False, skip_optdeps=skip_optdeps)

        if res:
            if os.path.exists(pkg.get_disk_data_path()):
                with open(pkg.get_disk_data_path()) as f:
                    data = f.read()
                    if data:
                        data = json.loads(data)
                        pkg.fill_cached_data(data)

        return res

    def _is_wget_available(self):
        try:
            new_subprocess(['wget', '--version'])
            return True
        except FileNotFoundError:
            return False

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def can_work(self) -> bool:
        try:
            return self.arch_distro and pacman.is_enabled() and self._is_wget_available()
        except FileNotFoundError:
            return False

    def is_downgrade_enabled(self) -> bool:
        try:
            new_subprocess(['git', '--version'])
            return True
        except FileNotFoundError:
            return False

    def cache_to_disk(self, pkg: ArchPackage, icon_bytes: bytes, only_icon: bool):
       pass

    def requires_root(self, action: str, pkg: ArchPackage):
        return action != 'search'

    def prepare(self):
        self.dcache_updater.start()
        self.comp_optimizer.start()
        self.aur_index_updater.start()

    def list_updates(self, internet_available: bool) -> List[PackageUpdate]:
        installed = self.read_installed(disk_loader=None, internet_available=internet_available).installed
        return [PackageUpdate(p.id, p.latest_version, 'aur') for p in installed if p.update]

    def list_warnings(self) -> List[str]:
        warnings = []

        if self.arch_distro:
            if not pacman.is_enabled():
                warnings.append(self.i18n['arch.warning.disabled'].format(bold('pacman')))

            if not self._is_wget_available():
                warnings.append(self.i18n['arch.warning.disabled'].format(bold('wget')))

            if not git.is_enabled():
                warnings.append(self.i18n['arch.warning.git'].format(bold('git')))

        return warnings

    def list_suggestions(self, limit: int) -> List[PackageSuggestion]:
        res = []

        sugs = [(i, p) for i, p in suggestions.ALL.items()]
        sugs.sort(key=lambda t: t[1].value, reverse=True)

        if limit > 0:
            sugs = sugs[0:limit]

        sug_names = {s[0] for s in sugs}

        api_res = self.aur_client.get_info(sug_names)

        if api_res:
            for pkg in api_res:
                if pkg.get('Name') in sug_names:
                    res.append(PackageSuggestion(self.mapper.map_api_data(pkg, {}), suggestions.ALL.get(pkg['Name'])))

        return res

    def is_default_enabled(self) -> bool:
        return False

    def launch(self, pkg: ArchPackage):
        if pkg.command:
            subprocess.Popen(pkg.command.split(' '))
