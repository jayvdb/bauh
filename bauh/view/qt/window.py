import operator
import time
from functools import reduce
from pathlib import Path
from typing import List, Type, Set

from PyQt5.QtCore import QEvent, Qt, QSize, pyqtSignal
from PyQt5.QtGui import QIcon, QWindowStateChangeEvent, QPixmap, QCursor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QCheckBox, QHeaderView, QToolBar, \
    QLabel, QPlainTextEdit, QLineEdit, QProgressBar, QPushButton, QComboBox, QMenu, QAction, QApplication

from bauh.api.abstract.cache import MemoryCache
from bauh.api.abstract.context import ApplicationContext
from bauh.api.abstract.controller import SoftwareManager
from bauh.api.abstract.model import SoftwarePackage, PackageAction
from bauh.api.abstract.view import MessageType
from bauh.view.core.config import Configuration
from bauh.view.core.controller import GenericSoftwareManager
from bauh.view.util import util, resource
from bauh.view.qt import dialog, commons, qt_utils
from bauh.view.qt.about import AboutDialog
from bauh.view.qt.apps_table import AppsTable, UpdateToggleButton
from bauh.view.qt.components import new_spacer, InputFilter, IconButton
from bauh.view.qt.confirmation import ConfirmationDialog
from bauh.view.qt.gem_selector import GemSelectorPanel
from bauh.view.qt.history import HistoryDialog
from bauh.view.qt.info import InfoDialog
from bauh.view.qt.root import is_root, ask_root_password
from bauh.view.qt.styles import StylesComboBox
from bauh.view.qt.thread import UpdateSelectedApps, RefreshApps, UninstallApp, DowngradeApp, GetAppInfo, \
    GetAppHistory, SearchPackages, InstallPackage, AnimateProgress, VerifyModels, FindSuggestions, ListWarnings, \
    AsyncAction, LaunchApp, ApplyFilters, CustomAction
from bauh.view.qt.view_model import PackageView
from bauh.view.qt.view_utils import load_icon

DARK_ORANGE = '#FF4500'


def toolbar_button_style(bg: str):
    return 'QPushButton { color: white; font-weight: bold; background: ' + bg + '}'


class ManageWindow(QWidget):
    __BASE_HEIGHT__ = 400

    signal_user_res = pyqtSignal(bool)
    signal_table_update = pyqtSignal()

    def __init__(self, i18n: dict, icon_cache: MemoryCache, manager: SoftwareManager, disk_cache: bool,
                 download_icons: bool, screen_size, suggestions: bool, display_limit: int, config: Configuration,
                 context: ApplicationContext, notifications: bool, tray_icon=None):
        super(ManageWindow, self).__init__()
        self.i18n = i18n
        self.manager = manager
        self.tray_icon = tray_icon
        self.working = False  # restrict the number of threaded actions
        self.pkgs = []  # packages current loaded in the table
        self.pkgs_available = []  # all packages loaded in memory
        self.pkgs_installed = []  # cached installed packages
        self.display_limit = display_limit
        self.icon_cache = icon_cache
        self.disk_cache = disk_cache
        self.download_icons = download_icons
        self.screen_size = screen_size
        self.config = config
        self.context = context
        self.notifications = notifications

        self.icon_app = QIcon(resource.get_path('img/logo.svg'))
        self.resize(ManageWindow.__BASE_HEIGHT__, ManageWindow.__BASE_HEIGHT__)
        self.setWindowIcon(self.icon_app)

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.toolbar_top = QToolBar()
        self.toolbar_top.addWidget(new_spacer())

        self.label_status = QLabel()
        self.label_status.setText('')
        self.label_status.setStyleSheet("font-weight: bold")
        self.toolbar_top.addWidget(self.label_status)

        self.toolbar_search = QToolBar()
        self.toolbar_search.setStyleSheet("spacing: 0px;")
        self.toolbar_search.setContentsMargins(0, 0, 0, 0)

        label_pre_search = QLabel()
        label_pre_search.setStyleSheet("background: white; border-top-left-radius: 5px; border-bottom-left-radius: 5px;")
        self.toolbar_search.addWidget(label_pre_search)

        self.input_search = QLineEdit()
        self.input_search.setMaxLength(20)
        self.input_search.setFrame(False)
        self.input_search.setPlaceholderText(self.i18n['window_manage.input_search.placeholder'] + "...")
        self.input_search.setToolTip(self.i18n['window_manage.input_search.tooltip'])
        self.input_search.setStyleSheet("QLineEdit { background-color: white; color: gray; spacing: 0; height: 30px; font-size: 12px; width: 300px}")
        self.input_search.returnPressed.connect(self.search)
        self.toolbar_search.addWidget(self.input_search)

        label_pos_search = QLabel()
        label_pos_search.setPixmap(QPixmap(resource.get_path('img/search.svg')))
        label_pos_search.setStyleSheet("background: white; padding-right: 10px; border-top-right-radius: 5px; border-bottom-right-radius: 5px;")
        self.toolbar_search.addWidget(label_pos_search)

        self.ref_toolbar_search = self.toolbar_top.addWidget(self.toolbar_search)
        self.toolbar_top.addWidget(new_spacer())
        self.layout.addWidget(self.toolbar_top)

        self.toolbar = QToolBar()
        self.toolbar.setStyleSheet('QToolBar {spacing: 4px; margin-top: 15px; margin-bottom: 5px}')

        self.checkbox_updates = QCheckBox()
        self.checkbox_updates.setText(self.i18n['updates'].capitalize())
        self.checkbox_updates.stateChanged.connect(self._handle_updates_filter)
        self.ref_checkbox_updates = self.toolbar.addWidget(self.checkbox_updates)

        self.checkbox_only_apps = QCheckBox()
        self.checkbox_only_apps.setText(self.i18n['manage_window.checkbox.only_apps'])
        self.checkbox_only_apps.setChecked(True)
        self.checkbox_only_apps.stateChanged.connect(self._handle_filter_only_apps)
        self.ref_checkbox_only_apps = self.toolbar.addWidget(self.checkbox_only_apps)

        self.any_type_filter = 'any'
        self.cache_type_filter_icons = {}
        self.combo_filter_type = QComboBox()
        self.combo_filter_type.setStyleSheet('QLineEdit { height: 2px}')
        self.combo_filter_type.setEditable(True)
        self.combo_filter_type.lineEdit().setReadOnly(True)
        self.combo_filter_type.lineEdit().setAlignment(Qt.AlignCenter)
        self.combo_filter_type.activated.connect(self._handle_type_filter)
        self.combo_filter_type.addItem(load_icon(resource.get_path('img/logo.svg'), 14), self.i18n[self.any_type_filter].capitalize(), self.any_type_filter)
        self.ref_combo_filter_type = self.toolbar.addWidget(self.combo_filter_type)

        self.input_name_filter = InputFilter(self.apply_filters_async)
        self.input_name_filter.setMaxLength(10)
        self.input_name_filter.setPlaceholderText(self.i18n['manage_window.name_filter.placeholder'] + '...')
        self.input_name_filter.setToolTip(self.i18n['manage_window.name_filter.tooltip'])
        self.input_name_filter.setStyleSheet("QLineEdit { background-color: white; color: gray;}")
        self.input_name_filter.setFixedWidth(130)
        self.ref_input_name_filter = self.toolbar.addWidget(self.input_name_filter)

        self.toolbar.addWidget(new_spacer())

        self.bt_installed = QPushButton()
        self.bt_installed.setToolTip(self.i18n['manage_window.bt.installed.tooltip'])
        self.bt_installed.setIcon(QIcon(resource.get_path('img/disk.png')))
        self.bt_installed.setText(self.i18n['manage_window.bt.installed.text'].capitalize())
        self.bt_installed.clicked.connect(self._show_installed)
        self.bt_installed.setStyleSheet(toolbar_button_style('#A94E0A'))
        self.ref_bt_installed = self.toolbar.addWidget(self.bt_installed)

        self.bt_refresh = QPushButton()
        self.bt_refresh.setToolTip(i18n['manage_window.bt.refresh.tooltip'])
        self.bt_refresh.setIcon(QIcon(resource.get_path('img/refresh.svg')))
        self.bt_refresh.setText(self.i18n['manage_window.bt.refresh.text'])
        self.bt_refresh.setStyleSheet(toolbar_button_style('#2368AD'))
        self.bt_refresh.clicked.connect(lambda: self.refresh_apps(keep_console=False))
        self.ref_bt_refresh = self.toolbar.addWidget(self.bt_refresh)

        self.bt_upgrade = QPushButton()
        self.bt_upgrade.setToolTip(i18n['manage_window.bt.upgrade.tooltip'])
        self.bt_upgrade.setIcon(QIcon(resource.get_path('img/app_update.svg')))
        self.bt_upgrade.setText(i18n['manage_window.bt.upgrade.text'])
        self.bt_upgrade.setStyleSheet(toolbar_button_style('#20A435'))
        self.bt_upgrade.clicked.connect(self.update_selected)
        self.ref_bt_upgrade = self.toolbar.addWidget(self.bt_upgrade)

        self.layout.addWidget(self.toolbar)

        self.table_apps = AppsTable(self, self.icon_cache, disk_cache=self.disk_cache, download_icons=self.download_icons)
        self.table_apps.change_headers_policy()

        self.layout.addWidget(self.table_apps)

        toolbar_console = QToolBar()

        self.checkbox_console = QCheckBox()
        self.checkbox_console.setText(self.i18n['manage_window.checkbox.show_details'])
        self.checkbox_console.stateChanged.connect(self._handle_console)
        self.checkbox_console.setVisible(False)
        self.ref_checkbox_console = toolbar_console.addWidget(self.checkbox_console)

        toolbar_console.addWidget(new_spacer())

        self.label_displayed = QLabel()
        toolbar_console.addWidget(self.label_displayed)

        self.layout.addWidget(toolbar_console)

        self.textarea_output = QPlainTextEdit(self)
        self.textarea_output.resize(self.table_apps.size())
        self.textarea_output.setStyleSheet("background: black; color: white;")
        self.layout.addWidget(self.textarea_output)
        self.textarea_output.setVisible(False)
        self.textarea_output.setReadOnly(True)

        self.toolbar_substatus = QToolBar()
        self.toolbar_substatus.addWidget(new_spacer())
        self.label_substatus = QLabel()
        self.toolbar_substatus.addWidget(self.label_substatus)
        self.toolbar_substatus.addWidget(new_spacer())
        self.layout.addWidget(self.toolbar_substatus)
        self._change_label_substatus('')

        self.thread_update = self._bind_async_action(UpdateSelectedApps(self.manager, self.i18n), finished_call=self._finish_update_selected)
        self.thread_refresh = self._bind_async_action(RefreshApps(self.manager), finished_call=self._finish_refresh_apps, only_finished=True)
        self.thread_uninstall = self._bind_async_action(UninstallApp(self.manager, self.icon_cache), finished_call=self._finish_uninstall)
        self.thread_get_info = self._bind_async_action(GetAppInfo(self.manager), finished_call=self._finish_get_info)
        self.thread_get_history = self._bind_async_action(GetAppHistory(self.manager, self.i18n), finished_call=self._finish_get_history)
        self.thread_search = self._bind_async_action(SearchPackages(self.manager), finished_call=self._finish_search, only_finished=True)
        self.thread_downgrade = self._bind_async_action(DowngradeApp(self.manager, self.i18n), finished_call=self._finish_downgrade)
        self.thread_suggestions = self._bind_async_action(FindSuggestions(man=self.manager), finished_call=self._finish_search, only_finished=True)
        self.thread_run_app = self._bind_async_action(LaunchApp(self.manager), finished_call=self._finish_run_app, only_finished=False)
        self.thread_custom_action = self._bind_async_action(CustomAction(manager=self.manager), finished_call=self._finish_custom_action)

        self.thread_apply_filters = ApplyFilters()
        self.thread_apply_filters.signal_finished.connect(self._finish_apply_filters_async)
        self.thread_apply_filters.signal_table.connect(self._update_table_and_upgrades)
        self.signal_table_update.connect(self.thread_apply_filters.stop_waiting)

        self.thread_install = InstallPackage(manager=self.manager, disk_cache=self.disk_cache, icon_cache=self.icon_cache, locale_keys=self.i18n)
        self._bind_async_action(self.thread_install, finished_call=self._finish_install)

        self.thread_animate_progress = AnimateProgress()
        self.thread_animate_progress.signal_change.connect(self._update_progress)

        self.thread_verify_models = VerifyModels()
        self.thread_verify_models.signal_updates.connect(self._notify_model_data_change)

        self.toolbar_bottom = QToolBar()
        self.toolbar_bottom.setIconSize(QSize(16, 16))
        self.toolbar_bottom.setStyleSheet('QToolBar { spacing: 3px }')

        self.toolbar_bottom.addWidget(new_spacer())

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(10 if QApplication.instance().style().objectName().lower() == 'windows' else 4)

        self.progress_bar.setTextVisible(False)
        self.ref_progress_bar = self.toolbar_bottom.addWidget(self.progress_bar)

        self.toolbar_bottom.addWidget(new_spacer())

        self.combo_styles = StylesComboBox(parent=self, i18n=i18n, show_panel_after_restart=bool(tray_icon))
        self.combo_styles.setStyleSheet('QComboBox {font-size: 12px;}')
        self.ref_combo_styles = self.toolbar_bottom.addWidget(self.combo_styles)

        bt_settings = IconButton(icon_path=resource.get_path('img/app_settings.svg'),
                                 action=self._show_settings_menu,
                                 background='#12ABAB',
                                 tooltip=self.i18n['manage_window.bt_settings.tooltip'])
        self.ref_bt_settings = self.toolbar_bottom.addWidget(bt_settings)

        self.layout.addWidget(self.toolbar_bottom)

        qt_utils.centralize(self)

        self.filter_only_apps = True
        self.type_filter = self.any_type_filter
        self.filter_updates = False
        self._maximized = False
        self.progress_controll_enabled = True
        self.recent_installation = False

        self.dialog_about = None
        self.first_refresh = suggestions

        self.thread_warnings = ListWarnings(man=manager, locale_keys=i18n)
        self.thread_warnings.signal_warnings.connect(self._show_warnings)

    def set_tray_icon(self, tray_icon):
        self.tray_icon = tray_icon
        self.combo_styles.show_panel_after_restart = bool(tray_icon)

    def _update_process_progress(self, val: int):
        if self.progress_controll_enabled:
            self.thread_animate_progress.set_progress(val)

    def apply_filters_async(self):
        self.label_status.setText(self.i18n['manage_window.status.filtering'] + '...')

        self.ref_toolbar_search.setVisible(False)

        if self.ref_input_name_filter.isVisible():
            self.input_name_filter.setReadOnly(True)

        self.thread_apply_filters.filters = self._gen_filters()
        self.thread_apply_filters.pkgs = self.pkgs_available
        self.thread_apply_filters.start()

    def _update_table_and_upgrades(self, pkgs_info: dict):
        self._update_table(pkgs_info=pkgs_info, signal=True)
        self.update_bt_upgrade(pkgs_info)

    def _finish_apply_filters_async(self, success: bool):
        self.label_status.setText('')
        self.ref_toolbar_search.setVisible(True)

        if self.ref_input_name_filter.isVisible():
            self.input_name_filter.setReadOnly(False)

    def _bind_async_action(self, action: AsyncAction, finished_call, only_finished: bool = False) -> AsyncAction:
        action.signal_finished.connect(finished_call)

        if not only_finished:
            action.signal_confirmation.connect(self._ask_confirmation)
            action.signal_output.connect(self._update_action_output)
            action.signal_message.connect(self._show_message)
            action.signal_status.connect(self._change_label_status)
            action.signal_substatus.connect(self._change_label_substatus)
            action.signal_progress.connect(self._update_process_progress)

            self.signal_user_res.connect(action.confirm)

        return action

    def _ask_confirmation(self, msg: dict):
        self.thread_animate_progress.pause()
        diag = ConfirmationDialog(title=msg['title'],
                                  body=msg['body'],
                                  locale_keys=self.i18n,
                                  components=msg['components'],
                                  confirmation_label=msg['confirmation_label'],
                                  deny_label=msg['deny_label'])
        res = diag.is_confirmed()
        self.thread_animate_progress.animate()
        self.signal_user_res.emit(res)

    def _show_message(self, msg: dict):
        self.thread_animate_progress.pause()
        dialog.show_message(title=msg['title'], body=msg['body'], type_=msg['type'])
        self.thread_animate_progress.animate()

    def _show_warnings(self, warnings: List[str]):
        if warnings:
            dialog.show_message(title=self.i18n['warning'].capitalize(), body='<p>{}</p>'.format('<br/><br/>'.join(warnings)), type_=MessageType.WARNING)

    def show(self):
        super(ManageWindow, self).show()
        if not self.thread_warnings.isFinished():
            self.thread_warnings.start()

    def verify_warnings(self):
        self.thread_warnings.start()

    def _show_installed(self):
        if self.pkgs_installed:
            self.finish_action()
            self.ref_bt_upgrade.setVisible(True)
            self.ref_checkbox_only_apps.setVisible(True)
            self.input_search.setText('')
            self.input_name_filter.setText('')
            self.update_pkgs(new_pkgs=None, as_installed=True)

    def _show_about(self):
        if self.dialog_about is None:
            self.dialog_about = AboutDialog(self.i18n)

        self.dialog_about.show()

    def _handle_updates_filter(self, status: int):
        self.filter_updates = status == 2
        self.apply_filters_async()

    def _handle_filter_only_apps(self, status: int):
        self.filter_only_apps = status == 2
        self.apply_filters_async()

    def _handle_type_filter(self, idx: int):
        self.type_filter = self.combo_filter_type.itemData(idx)
        self.apply_filters_async()

    def _notify_model_data_change(self):
        self.table_apps.fill_async_data()

    def changeEvent(self, e: QEvent):
        if isinstance(e, QWindowStateChangeEvent):
            self._maximized = self.isMaximized()
            policy = QHeaderView.Stretch if self._maximized else QHeaderView.ResizeToContents
            self.table_apps.change_headers_policy(policy)

    def closeEvent(self, event):

        if self.tray_icon:
            event.ignore()
            self.hide()
            self._handle_console_option(False)

    def _handle_console(self, checked: bool):

        if checked:
            self.textarea_output.show()
        else:
            self.textarea_output.hide()

    def _handle_console_option(self, enable: bool):

        if enable:
            self.textarea_output.clear()

        self.ref_checkbox_console.setVisible(enable)
        self.checkbox_console.setChecked(False)
        self.textarea_output.hide()

    def refresh_apps(self, keep_console: bool = True, top_app: PackageView = None, pkg_types: Set[Type[SoftwarePackage]] = None):
        self.recent_installation = False
        self.type_filter = None
        self.input_search.clear()

        if not keep_console:
            self._handle_console_option(False)

        self.ref_checkbox_updates.setVisible(False)
        self.ref_checkbox_only_apps.setVisible(False)
        self._begin_action(self.i18n['manage_window.status.refreshing'], keep_bt_installed=False, clear_filters=True)

        self.thread_refresh.app = top_app  # the app will be on top when refresh happens
        self.thread_refresh.pkg_types = pkg_types
        self.thread_refresh.start()

    def _finish_refresh_apps(self, res: dict, as_installed: bool = True):
        self.finish_action()
        self.ref_checkbox_only_apps.setVisible(bool(res['installed']))
        self.ref_bt_upgrade.setVisible(True)
        self.update_pkgs(res['installed'], as_installed=as_installed, types=res['types'])
        self.first_refresh = False

    def uninstall_app(self, app: PackageView):
        pwd = None
        requires_root = self.manager.requires_root('uninstall', app.model)

        if not is_root() and requires_root:
            pwd, ok = ask_root_password(self.i18n)

            if not ok:
                return

        self._handle_console_option(True)
        self._begin_action('{} {}'.format(self.i18n['manage_window.status.uninstalling'], app.model.name))

        self.thread_uninstall.app = app
        self.thread_uninstall.root_password = pwd
        self.thread_uninstall.start()

    def run_app(self, app: PackageView):
        self._begin_action(self.i18n['manage_window.status.running_app'].format(app.model.name))
        self.thread_run_app.app = app
        self.thread_run_app.start()

    def _finish_uninstall(self, pkgv: PackageView):
        self.finish_action()

        if pkgv:
            if self._can_notify_user():
                util.notify_user('{} ({}) {}'.format(pkgv.model.name, pkgv.model.get_type(), self.i18n['uninstalled']))

            self.refresh_apps(pkg_types={pkgv.model.__class__})
        else:
            if self._can_notify_user():
                util.notify_user('{}: {}'.format(pkgv.model.name, self.i18n['notification.uninstall.failed']))

            self.checkbox_console.setChecked(True)

    def _can_notify_user(self):
        return self.notifications and (self.isHidden() or self.isMinimized())

    def _finish_downgrade(self, res: dict):
        self.finish_action()

        if res['success']:
            if self._can_notify_user():
                util.notify_user('{} {}'.format(res['app'], self.i18n['downgraded']))

            self.refresh_apps(pkg_types={res['app'].model.__class__})

            if self.tray_icon:
                self.tray_icon.verify_updates(notify_user=False)
        else:
            if self._can_notify_user():
                util.notify_user(self.i18n['notification.downgrade.failed'])

            self.checkbox_console.setChecked(True)

    def _change_label_status(self, status: str):
        self.label_status.setText(status)

    def _change_label_substatus(self, substatus: str):
        self.label_substatus.setText('<p>{}</p>'.format(substatus))
        if not substatus:
            self.toolbar_substatus.hide()
        elif not self.toolbar_substatus.isVisible():
            self.toolbar_substatus.show()

    def _update_table(self, pkgs_info: dict, signal: bool = False):
        self.pkgs = pkgs_info['pkgs_displayed']

        self.table_apps.update_pkgs(self.pkgs, update_check_enabled=pkgs_info['not_installed'] == 0)

        if not self._maximized:
            self.table_apps.change_headers_policy(QHeaderView.Stretch)
            self.table_apps.change_headers_policy()
            self.resize_and_center(accept_lower_width=len(self.pkgs) > 0)
            self.label_displayed.setText('{} / {}'.format(len(self.pkgs), len(self.pkgs_available)))
        else:
            self.label_displayed.setText('')

        if signal:
            self.signal_table_update.emit()

    def update_bt_upgrade(self, pkgs_info: dict = None):
        show_bt_upgrade = False

        if not pkgs_info or pkgs_info['not_installed'] == 0:
            for app_v in (pkgs_info['pkgs_displayed'] if pkgs_info else self.pkgs):
                if app_v.update_checked:
                    show_bt_upgrade = True
                    break

        self.ref_bt_upgrade.setVisible(show_bt_upgrade)

    def change_update_state(self, pkgs_info: dict, trigger_filters: bool = True):
        self.update_bt_upgrade(pkgs_info)

        if pkgs_info['updates'] > 0:

            if pkgs_info['not_installed'] == 0:
                if not self.ref_checkbox_updates.isVisible():
                    self.ref_checkbox_updates.setVisible(True)

                if not self.filter_updates:
                    self._change_checkbox(self.checkbox_updates, True, 'filter_updates', trigger_filters)

            if pkgs_info['napp_updates'] > 0 and self.filter_only_apps:
                self._change_checkbox(self.checkbox_only_apps, False, 'filter_only_apps', trigger_filters)
        else:
            self._change_checkbox(self.checkbox_updates, False, 'filter_updates', trigger_filters)

            self.ref_checkbox_updates.setVisible(False)

    def _change_checkbox(self, checkbox: QCheckBox, checked: bool, attr: str = None, trigger: bool = True):
        if not trigger:
            checkbox.blockSignals(True)

        checkbox.setChecked(checked)

        if not trigger:
            setattr(self, attr, checked)
            checkbox.blockSignals(False)

    def _gen_filters(self, updates: int = 0, ignore_updates: bool = False) -> dict:
        return {
            'only_apps': self.filter_only_apps,
            'type': self.type_filter,
            'updates': False if ignore_updates else self.filter_updates,
            'name': self.input_name_filter.get_text().lower() if self.input_name_filter.get_text() else None,
            'display_limit': self.display_limit if updates <= 0 else None
        }

    def update_pkgs(self, new_pkgs: List[SoftwarePackage], as_installed: bool, types: Set[type] = None, ignore_updates: bool = False):
        self.input_name_filter.setText('')
        pkgs_info = commons.new_pkgs_info()
        filters = self._gen_filters(ignore_updates)

        if new_pkgs is not None:
            old_installed = None

            if as_installed:
                old_installed = self.pkgs_installed
                self.pkgs_installed = []

            for pkg in new_pkgs:
                app_model = PackageView(model=pkg)
                commons.update_info(app_model, pkgs_info)
                commons.apply_filters(app_model, filters, pkgs_info)

            if old_installed and types:
                for pkgv in old_installed:
                    if not pkgv.model.__class__ in types:
                        commons.update_info(pkgv, pkgs_info)
                        commons.apply_filters(pkgv, filters, pkgs_info)

        else:  # use installed
            for pkgv in self.pkgs_installed:
                commons.update_info(pkgv, pkgs_info)
                commons.apply_filters(pkgv, filters, pkgs_info)

        if pkgs_info['apps_count'] == 0:
            if self.first_refresh:
                self._begin_search('')
                self.thread_suggestions.start()
                return
            else:
                self._change_checkbox(self.checkbox_only_apps, False, 'filter_only_apps', trigger=False)
                self.checkbox_only_apps.setCheckable(False)
        else:
            self.checkbox_only_apps.setCheckable(True)
            self._change_checkbox(self.checkbox_only_apps, True, 'filter_only_apps', trigger=False)

        self.change_update_state(pkgs_info=pkgs_info, trigger_filters=False)
        self._apply_filters(pkgs_info, ignore_updates=ignore_updates)
        self.change_update_state(pkgs_info=pkgs_info, trigger_filters=False)

        self.pkgs_available = pkgs_info['pkgs']

        if as_installed:
            self.pkgs_installed = pkgs_info['pkgs']

        self.pkgs = pkgs_info['pkgs_displayed']

        if self.pkgs:
            self.ref_input_name_filter.setVisible(True)

        self._update_type_filters(pkgs_info['available_types'])

        self._update_table(pkgs_info=pkgs_info)

        if new_pkgs:
            self.thread_verify_models.apps = self.pkgs
            self.thread_verify_models.start()

        if self.pkgs_installed:
            self.ref_bt_installed.setVisible(not as_installed and not self.recent_installation)

        self.resize_and_center(accept_lower_width=self.pkgs_installed)

    def _apply_filters(self, pkgs_info: dict, ignore_updates: bool):
        pkgs_info['pkgs_displayed'] = []
        filters = self._gen_filters(updates=pkgs_info['updates'], ignore_updates=ignore_updates)
        for pkgv in pkgs_info['pkgs']:
            commons.apply_filters(pkgv, filters, pkgs_info)

    def _update_type_filters(self, available_types: dict = None):

        if available_types is None:
            self.ref_combo_filter_type.setVisible(self.combo_filter_type.count() > 1)
        else:
            self.type_filter = self.any_type_filter

            if available_types and len(available_types) > 1:
                if self.combo_filter_type.count() > 1:
                    for _ in range(self.combo_filter_type.count() - 1):
                        self.combo_filter_type.removeItem(1)

                for app_type, icon_path in available_types.items():
                    icon = self.cache_type_filter_icons.get(app_type)

                    if not icon:
                        icon = load_icon(icon_path, 14)
                        self.cache_type_filter_icons[app_type] = icon

                    self.combo_filter_type.addItem(icon, app_type.capitalize(), app_type)

                self.ref_combo_filter_type.setVisible(True)
            else:
                self.ref_combo_filter_type.setVisible(False)

    def resize_and_center(self, accept_lower_width: bool = True):
        if self.pkgs:
            new_width = reduce(operator.add, [self.table_apps.columnWidth(i) for i in range(self.table_apps.columnCount())])

            if self.ref_bt_upgrade.isVisible() or self.ref_bt_settings.isVisible():
                new_width *= 1.07
        else:
            new_width = self.toolbar_top.width()

        if accept_lower_width or new_width > self.width():
            self.resize(new_width, self.height())

            if self.ref_bt_upgrade.isVisible() and self.bt_upgrade.visibleRegion().isEmpty():
                self.adjustSize()

        qt_utils.centralize(self)

    def update_selected(self):
        if self.pkgs:
            requires_root = False

            to_update = []

            for app_v in self.pkgs:
                if app_v.update_checked:
                    to_update.append(app_v)

                    if self.manager.requires_root('update', app_v.model):
                        requires_root = True

            if to_update and dialog.ask_confirmation(title=self.i18n['manage_window.upgrade_all.popup.title'],
                                                     body=self.i18n['manage_window.upgrade_all.popup.body'],
                                                     locale_keys=self.i18n,
                                                     widgets=[UpdateToggleButton(None, self, self.i18n, clickable=False)]):
                pwd = None

                if not is_root() and requires_root:
                    pwd, ok = ask_root_password(self.i18n)

                    if not ok:
                        return

                self._handle_console_option(True)
                self.progress_controll_enabled = len(to_update) == 1
                self._begin_action(self.i18n['manage_window.status.upgrading'])
                self.thread_update.apps_to_update = to_update
                self.thread_update.root_password = pwd
                self.thread_update.start()

    def _finish_update_selected(self, res: dict):
        self.finish_action()

        if res['success']:
            if self._can_notify_user():
                util.notify_user('{} {}'.format(res['updated'], self.i18n['notification.update_selected.success']))

            self.refresh_apps(pkg_types=res['types'])

            if self.tray_icon:
                self.tray_icon.verify_updates()
        else:
            if self._can_notify_user():
                util.notify_user(self.i18n['notification.update_selected.failed'])

            self.ref_bt_upgrade.setVisible(True)
            self.checkbox_console.setChecked(True)

    def _update_action_output(self, output: str):
        self.textarea_output.appendPlainText(output)

    def _begin_action(self, action_label: str, keep_search: bool = False, keep_bt_installed: bool = True, clear_filters: bool = False):
        self.ref_input_name_filter.setVisible(False)
        self.ref_combo_filter_type.setVisible(False)
        self.ref_bt_settings.setVisible(False)
        self.thread_animate_progress.stop = False
        self.thread_animate_progress.start()
        self.ref_progress_bar.setVisible(True)
        self.ref_combo_styles.setVisible(False)

        self.label_status.setText(action_label + "...")
        self.ref_bt_upgrade.setVisible(False)
        self.ref_bt_refresh.setVisible(False)
        self.checkbox_only_apps.setEnabled(False)
        self.table_apps.setEnabled(False)
        self.checkbox_updates.setEnabled(False)

        if not keep_bt_installed:
            self.ref_bt_installed.setVisible(False)
        elif self.ref_bt_installed.isVisible():
            self.ref_bt_installed.setEnabled(False)

        if keep_search:
            self.ref_toolbar_search.setVisible(True)
        else:
            self.ref_toolbar_search.setVisible(False)

        if clear_filters:
            self._update_type_filters({})
        else:
            self.combo_filter_type.setEnabled(False)

    def finish_action(self):
        self.ref_combo_styles.setVisible(True)
        self.thread_animate_progress.stop = True
        self.thread_animate_progress.wait()
        self.ref_progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)

        self._change_label_substatus('')
        self.ref_bt_settings.setVisible(True)

        self.ref_bt_refresh.setVisible(True)
        self.checkbox_only_apps.setEnabled(True)
        self.table_apps.setEnabled(True)
        self.input_search.setEnabled(True)
        self.label_status.setText('')
        self.label_substatus.setText('')
        self.ref_toolbar_search.setVisible(True)
        self.ref_toolbar_search.setEnabled(True)
        self.combo_filter_type.setEnabled(True)
        self.checkbox_updates.setEnabled(True)
        self.progress_controll_enabled = True

        if self.pkgs:
            self.ref_input_name_filter.setVisible(True)
            self.update_bt_upgrade()
            self._update_type_filters()

            if self.ref_bt_installed.isVisible():
                self.ref_bt_installed.setEnabled(True)

    def downgrade(self, pkgv: PackageView):
        pwd = None
        requires_root = self.manager.requires_root('downgrade', pkgv.model)

        if not is_root() and requires_root:
            pwd, ok = ask_root_password(self.i18n)

            if not ok:
                return

        self._handle_console_option(True)
        self._begin_action('{} {}'.format(self.i18n['manage_window.status.downgrading'], pkgv.model.name))

        self.thread_downgrade.app = pkgv
        self.thread_downgrade.root_password = pwd
        self.thread_downgrade.start()

    def get_app_info(self, pkg: dict):
        self._handle_console_option(False)
        self._begin_action(self.i18n['manage_window.status.info'])

        self.thread_get_info.app = pkg
        self.thread_get_info.start()

    def get_app_history(self, app: dict):
        self._handle_console_option(False)
        self._begin_action(self.i18n['manage_window.status.history'])

        self.thread_get_history.app = app
        self.thread_get_history.start()

    def _finish_get_info(self, app_info: dict):
        self.finish_action()
        dialog_info = InfoDialog(app=app_info, icon_cache=self.icon_cache, locale_keys=self.i18n, screen_size=self.screen_size)
        dialog_info.exec_()

    def _finish_get_history(self, res: dict):
        self.finish_action()

        if res.get('error'):
            self._handle_console_option(True)
            self.textarea_output.appendPlainText(res['error'])
            self.checkbox_console.setChecked(True)
        else:
            dialog_history = HistoryDialog(res['history'], self.icon_cache, self.i18n)
            dialog_history.exec_()

    def _begin_search(self, word):
        self._handle_console_option(False)
        self.ref_checkbox_only_apps.setVisible(False)
        self.ref_checkbox_updates.setVisible(False)
        self.filter_updates = False
        self._begin_action('{} {}'.format(self.i18n['manage_window.status.searching'], word if word else ''), clear_filters=True)

    def search(self):
        word = self.input_search.text().strip()
        if word:
            self._begin_search(word)
            self.thread_search.word = word
            self.thread_search.start()

    def _finish_search(self, res: dict):
        self.finish_action()

        if not res['error']:
            self.ref_bt_upgrade.setVisible(False)
            self.update_pkgs(res['pkgs_found'], as_installed=False, ignore_updates=True)
        else:
            dialog.show_message(title=self.i18n['warning'].capitalize(), body=self.i18n[res['error']], type_=MessageType.WARNING)

    def install(self, pkg: PackageView):
        pwd = None
        requires_root = self.manager.requires_root('install', pkg.model)

        if not is_root() and requires_root:
            pwd, ok = ask_root_password(self.i18n)

            if not ok:
                return

        self._handle_console_option(True)
        self._begin_action('{} {}'.format(self.i18n['manage_window.status.installing'], pkg.model.name))

        self.thread_install.pkg = pkg
        self.thread_install.root_password = pwd
        self.thread_install.start()

    def _finish_install(self, res: dict):
        self.input_search.setText('')
        self.finish_action()

        console_output = self.textarea_output.toPlainText()

        if console_output:
            log_path = '/tmp/bauh/logs/install/{}/{}'.format(res['pkg'].model.get_type(), res['pkg'].model.name)
            try:
                Path(log_path).mkdir(parents=True, exist_ok=True)

                log_file = log_path + '/{}.log'.format(int(time.time()))
                with open(log_file, 'w+') as f:
                    f.write(console_output)

                self.textarea_output.appendPlainText(self.i18n['console.install_logs.path'].format('"{}"'.format(log_file)))
            except:
                self.textarea_output.appendPlainText("[warning] Could not write install log file to '{}'".format(log_path))

        if res['success']:
            self.recent_installation = True
            if self._can_notify_user():
                util.notify_user(msg='{} ({}) {}'.format(res['pkg'].model.name, res['pkg'].model.get_type(), self.i18n['installed']))

            self._finish_refresh_apps({'installed': [res['pkg'].model], 'total': 1, 'types': None})
            self.ref_bt_installed.setVisible(False)
            self.ref_checkbox_only_apps.setVisible(False)
        else:
            if self._can_notify_user():
                util.notify_user('{}: {}'.format(res['pkg'].model.name, self.i18n['notification.install.failed']))

            self.checkbox_console.setChecked(True)

    def _update_progress(self, value: int):
        self.progress_bar.setValue(value)

    def _finish_run_app(self, success: bool):
        self.finish_action()

    def execute_custom_action(self, pkg: PackageView, action: PackageAction):
        pwd = None

        if not is_root() and action.requires_root:
            pwd, ok = ask_root_password(self.i18n)

            if not ok:
                return

        self._handle_console_option(True)
        self._begin_action('{} {}'.format(self.i18n[action.i18n_status_key], pkg.model.name))

        self.thread_custom_action.pkg = pkg
        self.thread_custom_action.root_password = pwd
        self.thread_custom_action.custom_action = action
        self.thread_custom_action.start()

    def _finish_custom_action(self, res: dict):
        self.finish_action()
        if res['success']:
            self.refresh_apps(pkg_types={res['pkg'].model.__class__})
        else:
            self.checkbox_console.setChecked(True)

    def show_gems_selector(self):
        gem_panel = GemSelectorPanel(window=self,
                                     manager=self.manager, i18n=self.i18n,
                                     config=self.config,
                                     show_panel_after_restart=bool(self.tray_icon))
        gem_panel.show()

    def _show_settings_menu(self):
        menu_row = QMenu()

        if isinstance(self.manager, GenericSoftwareManager):
            action_gems = QAction(self.i18n['manage_window.settings.gems'])
            action_gems.setIcon(self.icon_app)

            action_gems.triggered.connect(self.show_gems_selector)
            menu_row.addAction(action_gems)

        action_about = QAction(self.i18n['manage_window.settings.about'])
        action_about.setIcon(QIcon(resource.get_path('img/about.svg')))
        action_about.triggered.connect(self._show_about)
        menu_row.addAction(action_about)

        menu_row.adjustSize()
        menu_row.popup(QCursor.pos())
        menu_row.exec_()
