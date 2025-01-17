**bauh** ( ba-oo ) is a graphical user interface to manage your Linux applications ( packages ) ( old **fpakman** ). It currently supports Flatpak, Snap and AUR packaging types. When you launch **bauh** you will see
a management panel where you can search, update, install, uninstall and launch applications. You can also downgrade some applications depending on the package technology.

It has a **tray mode** (see **Settings** below) that attaches the application icon to the system tray providing a quick way to launch it. Also the icon will get red when updates are available.

This project has an official Twitter account ( **@bauh4linux** ) so people can stay on top of its news.


![management panel](https://raw.githubusercontent.com/vinifmor/bauh/master/pictures/panel.png)


### Developed with:
- Python3 and Qt5.

### Requirements

#### Debian-based distros
- **python3.5** or above
- **pip3**
- **python3-venv** ( only for **Manual installation** described below )

#### Arch-based distros
- **python**
- **python-requests**
- **python-pip**
- **python-pyqt5**

##### Optional
- **flatpak**: to be able to handle Flatpak applications
- **snapd**: to be able to handle Snap applications
- **pacman**: to be able to handle AUR packages
- **wget**: to be able to handle AUR packages
- **git**: to be able to downgrade AUR packages
- **aria2**: faster AUR source files downloading ( reduces packages installation time. More information below. )
- **libappindicator3**: for the **tray mode** in GTK3 desktop environments


### Distribution

**AUR**

As **bauh** package. There is also a staging version (**bauh-staging**) but is intended for testing and may not work properly.

**PyPi**

```pip3 install bauh ```

It may require **sudo**, but prefer the **Manual installation** described below to not mess up with your system libraries.


### Manual installation:
If you prefer a manual and isolated installation, type the following commands within the cloned project folder:

```
python3 -m venv env ( creates a virtualenv in a folder called **env** )
env/bin/pip install . ( install the application code inside the **env** )
env/bin/bauh  ( launch the application )
```

If you do not want to clone / download this repository, go to your **Home** folder and execute the commands above, but replace the second by ```env/bin/pip install bauh```.


### Autostart
In order to autostart the application, use your Desktop Environment settings to register it as a startup application / script (**bauh --tray=1**).


### Settings
You can change some application settings via environment variables or arguments (type ```bauh --help``` to get more information).
- **BAUH_SYSTEM_NOTIFICATIONS**: enable or disable system notifications. Use **0** (disable) or **1** (enable, default).
- **BAUH_CHECK_INTERVAL**: define the updates check interval in seconds. Default: 60.
- **BAUH_LOCALE**: define a custom app translation for a given locale key (e.g: 'pt', 'en', 'es', ...). Default: system locale.
- **BAUH_CACHE_EXPIRATION**: define a custom expiration time in SECONDS for cached API data. Default: 3600 (1 hour).
- **BAUH_ICON_EXPIRATION**: define a custom expiration time in SECONDS for cached icons. Default: 300 (5 minutes).
- **BAUH_DISK_CACHE**: enables / disables disk cache. When disk cache is enabled, the installed packages data are loaded faster. Use **0** (disable) or **1** (enable, default).
- **BAUH_DOWNLOAD_ICONS**: Enables / disables applications icons downloading. It may improve the application speed depending on how applications data are being retrieved. Use **0** (disable) or **1** (enable, default).
- **BAUH_CHECK_PACKAGING_ONCE**: If the availabilty of the supported packaging types should be checked only once. It improves the application speed if enabled, but can generate errors if you uninstall any packaging technology while using it, and every time a new supported packaging type is installed it will only be available after a restart. Use **0** (disable, default) or **1** (enable).
- **BAUH_TRAY**: If the tray icon and update-check daemon should be created. Use **0** (disable, default) or **1** (enable).
- **BAUH_SUGGESTIONS**: If application suggestions should be displayed if no package considered an application is installed (runtimes / libraries do not count as applications). Use **0** (disable) or **1** (enable, default).
- **BAUH_MAX_DISPLAYED**: Maximum number of displayed packages in the management panel table. Default: 50.
- **BAUH_LOGS**: enable **bauh** logs (for debugging purposes). Use: **0** (disable, default) or **1** (enable)
- **BAUH_DOWNLOAD_MULTITHREAD**: enable multi-threaded download for installation files ( only possible if **aria2** is installed ). This feature reduces applications installation time ( only supported by AUR packages at the moment ). Use **0** (disable) or **1** (enabled, default).

### How to improve **bauh** performance
- Disable package types that you do not want to deal with ( via GUI )
- If you don't care about restarting the app every time a new supported packaging technology is installed, set "check-packaging-once=1" (**bauh --check-packaging-once=1**). This can reduce the application response time up in some scenarios, since it won't need to recheck if the packaging type is available for every action you request.
- If you don't mind to see the applications icons, you can set "download-icons=0" (**bauh --download-icons=0**). The application may have a slight response improvement, since it will reduce the parallelism within it.
- Let the disk cache always enabled so **bauh** does not need to dynamically retrieve some data every time you launch it.

### Flatpak support ( flatpak gem )
- The user is able to search, install, uninstall, downgrade, laucnh and retrieve the applications history

### Snap support ( snap gem )
- The user is able to search, install, uninstall, refresh, launch and downgrade applications

### AUR support ( arch gem )
- It is **not enabled by default**
- The user is able to search, install, uninstall, downgrade, launch and retrieve the packages history
- It handles conflicts, and missing / optional packages installations ( including from your distro mirrors )
- If [**aria2**](https://github.com/aria2/aria2) is installed on your system and multi-threaded downloads are enabled ( see **BAUH_DOWNLOAD_MULTITHREAD** ), the source packages
will be pre-downloaded faster ( it does **NOT** modify your **pacman** settings ).
- Automatically makes simple package compilation improvements 

  a) if **MAKEFLAGS** is not set in **/etc/makepkg.conf** and **~/.makepkg.conf** does not exist,
then a copy of **/etc/makepkg.conf** will be generated at **~/.makepkg.conf** defining MAKEFLAGS to work with
the number of your machine processors (**-j${nproc}**).

  b) same as previous, but related to **COMPRESSXZ** definition ( if '--threads=0' is not defined )

Obs: this feature can be disabled through the environment variable **BAUH_ARCH_OPTIMIZE=0**
( For more information about these optimizations, check: https://wiki.archlinux.org/index.php/Makepkg )

### Files and Logs
- Some application settings are stored in **~/.config/bauh/config.json**
- Installation logs are saved at **/tmp/bauh/logs/install**
- Some data about your installed applications are stored in **~/.cache/bauh** to load them faster ( default behavior ).

### Code structure
#### Modules

**view**: code associated with the graphical interface

**gems**: code responsible to work with the different packaging technologies (every submodule deals with one or more types)

**api**: code abstractions representing the main actions that a user can do with Linux packages (search, install, ...). These abstractions are implemented by the **gems**, and
the **view** code is only attached to them (it does not know how the **gems** handle these actions)

**commons**: common code used by **gems** and **view**

### Roadmap
- Support for other packaging technologies
- Separate modules for each packaging technology
- Memory and performance improvements
- Improve user experience
