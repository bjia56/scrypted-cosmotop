import asyncio
import hashlib
import json
import os
import platform
import shutil
from typing import Any, AsyncGenerator, Callable
import urllib.request

import scrypted_sdk
from scrypted_sdk import ScryptedDeviceBase, DeviceProvider, StreamService, TTYSettings, ScryptedDeviceType, ScryptedInterface, Settings, Setting, Readme, Scriptable, ScriptSource


VERSON_JSON = open(os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'zip', 'unzipped', 'fs', 'cosmotop.json')).read()

COSMOTOP_VERSION = json.loads(VERSON_JSON)['version']
COSMOTOP_DOWNLOAD = f"https://github.com/bjia56/cosmotop/releases/download/{COSMOTOP_VERSION}/cosmotop"
DOWNLOAD_CACHE_BUST = f"{platform.system()}-{platform.machine()}-{COSMOTOP_VERSION}-0"

FILES_PATH = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files')
CACHEBUST_PATH = os.path.join(FILES_PATH, 'cachebust')


async def tail_f(file_path, check_interval=1):
    """
    Asynchronously tails a file like `tail -f`, yielding new content as it appears.

    :param file_path: Path to the file to tail.
    :param check_interval: Time in seconds to wait between checks for the file or new content.
    """
    file = None
    while True:
        if os.path.exists(file_path):
            if file is None:
                file = open(file_path, 'r')
                # Move to the end of the file
                file.seek(0, 2)
            line = file.readline()
            if line:
                yield line
            else:
                await asyncio.sleep(check_interval)
        else:
            if file is not None:
                file.close()
                file = None
            await asyncio.sleep(check_interval)


def name_hash(name):
    return hashlib.sha1(name.encode()).hexdigest()


class CosmotopPlugin(ScryptedDeviceBase, StreamService, DeviceProvider, TTYSettings, Settings):
    LOG_FILE = os.path.expanduser(f'~/.config/cosmotop/cosmotop.log')

    def __init__(self, nativeId: str = None, cluster_parent: 'CosmotopPlugin' = None) -> None:
        super().__init__(nativeId)

        self.downloaded = asyncio.ensure_future(self.do_download())
        self.log_loop = asyncio.create_task(self.tail_log_loop())

        self.cluster_parent = cluster_parent
        if not cluster_parent:
            self.discovered = asyncio.ensure_future(self.do_device_discovery())
            self.cluster_workers = {}
            self.cluster_worker_ids = {}

        self.config = CosmotopConfig("config", self)
        self.thememanager = CosmotopThemeManager("thememanager", self)

        async def alert_migration():
            if not cluster_parent and not self.migration_from_cosmotop_exe_alerted:
                await self.alert(f"The cosmotop executable is now downloaded as \"cosmotop{'.cmd' if platform.system() == 'Windows' else ''}\" instead of \"cosmotop.exe\". "
                                 "Please update any scripts and/or @scrypted/x11-camera accordingly.")
                self.migration_from_cosmotop_exe_alerted = True
        asyncio.create_task(alert_migration())

    @property
    def migration_from_cosmotop_exe_alerted(self) -> bool:
        try:
            alerted = self.storage.getItem('migration_from_cosmotop_exe_alerted')
            return bool(alerted)
        except Exception:
            return False
    @migration_from_cosmotop_exe_alerted.setter
    def migration_from_cosmotop_exe_alerted(self, value: bool) -> None:
        try:
            self.storage.setItem('migration_from_cosmotop_exe_alerted', value)
        except Exception as e:
            pass

    async def alert(self, msg) -> None:
        logger = await scrypted_sdk.systemManager.api.getLogger(self.nativeId)
        await logger.log("a", msg)

    async def lookup_worker_id(self, stable_id):
        return self.cluster_worker_ids[stable_id]

    async def do_download(self) -> None:
        self.exe = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files', 'cosmotop')

        if not self.shouldDownloadCosmotop():
            if platform.system() == 'Windows':
                self.exe += '.cmd'
            return

        shutil.rmtree(FILES_PATH, ignore_errors=True)
        self.downloadFile(COSMOTOP_DOWNLOAD, 'cosmotop')

        if platform.system() != 'Windows':
            os.chmod(self.exe, 0o755)
        else:
            os.rename(self.exe, self.exe + '.cmd')
            self.exe += '.cmd'

        with open(CACHEBUST_PATH, 'w') as f:
            f.write(DOWNLOAD_CACHE_BUST)

    def shouldDownloadCosmotop(self) -> bool:
        try:
            if not os.path.exists(CACHEBUST_PATH):
                return True
            with open(CACHEBUST_PATH, 'r') as f:
                return f.read() != DOWNLOAD_CACHE_BUST
        except:
            return True

    def downloadFile(self, url: str, filename: str, extract: Callable[[str, str], None] = None) -> str:
        try:
            fullpath = os.path.join(FILES_PATH, filename)
            if os.path.exists(fullpath):
                raise Exception(f"{fullpath} already exists")
            tmp = fullpath + '.tmp'
            print("Creating directory for", tmp)
            os.makedirs(os.path.dirname(fullpath), exist_ok=True)
            print("Downloading", url)
            response = urllib.request.urlopen(url)
            if response.getcode() < 200 or response.getcode() >= 300:
                raise Exception(f"Error downloading")
            read = 0
            with open(tmp, "wb") as f:
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    read += len(data)
                    print("Downloaded", read, "bytes")
                    f.write(data)
            if extract:
                extract(tmp, fullpath)
                os.remove(tmp)
            else:
                os.rename(tmp, fullpath)
            return fullpath
        except:
            print("Error downloading", url)
            import traceback
            traceback.print_exc()
            raise

    async def do_device_discovery(self) -> None:
        await self.downloaded
        devices = [
            {
                "nativeId": "config",
                "name": "Configuration",
                "type": ScryptedDeviceType.API.value,
                "interfaces": [
                    ScryptedInterface.Readme.value,
                    ScryptedInterface.Scriptable.value,
                ],
            },
            {
                "nativeId": "thememanager",
                "name": "Theme Manager",
                "type": ScryptedDeviceType.API.value,
                "interfaces": [
                    ScryptedInterface.Readme.value,
                    ScryptedInterface.Settings.value,
                ],
            }
        ]

        if scrypted_sdk.clusterManager:
            workers = await scrypted_sdk.clusterManager.getClusterWorkers()
            for worker_id in list(workers.keys()):
                worker = workers[worker_id]
                if worker['mode'] == 'server':
                    continue

                stable_id_base = name_hash(worker['name']) # the worker id could change, so treat the name as stable
                stable_id = stable_id_base
                ctr = 1
                while stable_id in self.cluster_worker_ids:
                    stable_id = f"{stable_id_base}-{ctr}"
                    ctr += 1

                self.cluster_worker_ids[stable_id] = worker_id

                devices.append({
                    "nativeId": stable_id,
                    "name": "cosmotop on " + worker['name'],
                    "type": ScryptedDeviceType.API.value,
                    "interfaces": [
                        ScryptedInterface.StreamService.value,
                        ScryptedInterface.TTY.value,
                        ScryptedInterface.Settings.value,
                    ],
                })

        await scrypted_sdk.deviceManager.onDevicesChanged({
            "devices": devices,
            "providerNativeId": self.nativeId,
        })

        if scrypted_sdk.clusterManager:
            for worker_id in list(workers.keys()):
                worker = workers[worker_id]
                if worker['mode'] == 'server':
                    continue

                # get the stable id from the map
                stable_id = None
                for k, v in self.cluster_worker_ids.items():
                    if v == worker_id:
                        stable_id = k
                        break

                fork = scrypted_sdk.fork({ 'clusterWorkerId': worker_id })
                result = await fork.result
                self.cluster_workers[stable_id] = await result.newCosmotopPlugin(stable_id, self)

    async def tail_log_loop(self):
        await self.downloaded
        self.print("--- Tailing log file ---")
        async for line in tail_f(CosmotopPlugin.LOG_FILE):
            self.print(line, end='')

    async def getDevice(self, nativeId: str) -> Any:
        await self.discovered

        if nativeId == "config":
            return self.config
        if nativeId == "thememanager":
            return self.thememanager

        if nativeId in self.cluster_workers:
            return self.cluster_workers[nativeId]

        # Management ui v2's PtyComponent expects the plugin device to implement
        # DeviceProvider and return the StreamService device via getDevice.
        return self

    async def connectStream(self, input: AsyncGenerator[Any, Any] = None, options: Any = None) -> Any:
        core = scrypted_sdk.systemManager.getDeviceByName("@scrypted/core")
        termsvc = await core.getDevice("terminalservice")
        if self.cluster_parent and scrypted_sdk.clusterManager:
            worker_id = await self.cluster_parent.lookup_worker_id(self.nativeId)
            termsvc = await termsvc.forkInterface(ScryptedInterface.StreamService.value, { 'clusterWorkerId': worker_id })
        else:
            termsvc = await scrypted_sdk.sdk.connectRPCObject(termsvc)
        return await termsvc.connectStream(input, {
            'cmd': [self.exe],
        })

    async def getTTYSettings(self) -> Any:
        return {
            "paths": [os.path.dirname(self.exe)],
        }

    async def getSettings(self) -> list[Setting]:
        await self.downloaded
        await self.config.config_reconciled

        return [
            {
                "key": "cosmotop_executable",
                "title": "cosmotop Path",
                "description": f"Path to the downloaded cosmotop{'.cmd' if platform.system() == 'Windows' else ''} executable.",
                "value": self.exe,
                "readonly": True,
            },
        ]

    async def putSetting(self, key: str, value: str) -> None:
        pass


class CosmotopConfig(ScryptedDeviceBase, Scriptable, Readme):
    CONFIG_PATH = os.path.expanduser(f'~/.config/cosmotop/cosmotop.conf')
    HOME_THEMES_DIR = os.path.expanduser(f'~/.config/cosmotop/themes')

    def __init__(self, nativeId: str, parent: CosmotopPlugin) -> None:
        super().__init__(nativeId)
        self.parent = parent
        self.cluster_parent_config = asyncio.ensure_future(parent.cluster_parent.getDevice("config")) if parent.cluster_parent else None
        self.default_config = asyncio.ensure_future(self.load_default_config())
        self.config_reconciled = asyncio.ensure_future(self.reconcile_from_disk())
        self.themes = []

    # can be called from forks
    async def load_default_config(self) -> str:
        await self.parent.downloaded
        cosmotop = self.parent.exe
        assert cosmotop is not None

        if platform.system() == 'Windows':
            child = await asyncio.create_subprocess_exec(cosmotop, '--show-defaults', stdout=asyncio.subprocess.PIPE)
        else:
            child = await asyncio.create_subprocess_exec('sh', cosmotop, '--show-defaults', stdout=asyncio.subprocess.PIPE)
        stdout, _ = await child.communicate()

        return stdout.decode()

    # can be called from forks
    async def reconcile_from_disk(self) -> None:
        await self.parent.downloaded
        await self.parent.thememanager.themes_loaded

        try:
            cosmotop = self.parent.exe
            assert cosmotop is not None

            if not os.path.exists(CosmotopConfig.CONFIG_PATH):
                os.makedirs(os.path.dirname(CosmotopConfig.CONFIG_PATH), exist_ok=True)
                with open(CosmotopConfig.CONFIG_PATH, 'w') as f:
                    f.write(await self.default_config)
            self.print(f"Using config file: {CosmotopConfig.CONFIG_PATH}")

            with open(CosmotopConfig.CONFIG_PATH) as f:
                data = f.read()

            if self.cluster_parent_config is not None:
                cluster_parent_config = await self.cluster_parent_config
                if data != await cluster_parent_config.get_config():
                    with open(CosmotopConfig.CONFIG_PATH, 'w') as f:
                        f.write(await cluster_parent_config.get_config())
            else:
                while self.storage is None:
                    await asyncio.sleep(1)

                if self.storage.getItem('config') and data != await self.get_config():
                    with open(CosmotopConfig.CONFIG_PATH, 'w') as f:
                        f.write(await self.get_config())

                if not self.storage.getItem('config'):
                    self.storage.setItem('config', data)

                self.print(f"Using themes dir: {CosmotopConfig.HOME_THEMES_DIR}")
                if platform.system() == 'Windows':
                    child = await asyncio.create_subprocess_exec(cosmotop, '--show-themes', stdout=asyncio.subprocess.PIPE)
                else:
                    child = await asyncio.create_subprocess_exec('sh', cosmotop, '--show-themes', stdout=asyncio.subprocess.PIPE)
                stdout, _ = await child.communicate()
                self.system_themes = []
                self.bundled_themes = []
                self.user_themes = []
                loading_themes_to = None
                for line in stdout.decode().splitlines():
                    if "System themes:" in line:
                        loading_themes_to = self.system_themes
                    elif "Bundled themes:" in line:
                        loading_themes_to = self.bundled_themes
                    elif "User themes:" in line:
                        loading_themes_to = self.user_themes
                    elif loading_themes_to is not None and line.strip():
                        loading_themes_to.append(line.strip())

                await self.onDeviceEvent(ScryptedInterface.Readme.value, None)
                await self.onDeviceEvent(ScryptedInterface.Scriptable.value, None)
        except:
            import traceback
            traceback.print_exc()

    # can be called from forks
    async def get_config(self) -> str:
        if self.cluster_parent_config is not None:
            return await (await self.cluster_parent_config).get_config()
        if self.storage:
            return self.storage.getItem('config') or await self.default_config
        return await self.default_config

    # should only be called on the primary plugin instance
    async def eval(self, source: ScriptSource, variables: Any = None) -> Any:
        raise Exception("cosmotop configuration cannot be evaluated")

    # should only be called on the primary plugin instance
    async def loadScripts(self) -> Any:
        await self.config_reconciled

        return {
            "cosmotop.conf": {
                "name": "cosmotop Configuration",
                "script": await self.get_config(),
                "language": "ini",
            }
        }

    # should only be called on the primary plugin instance
    async def saveScript(self, script: ScriptSource) -> None:
        await self.config_reconciled

        self.storage.setItem('config', script['script'])
        await self.onDeviceEvent(ScryptedInterface.Scriptable.value, None)

        updated = False
        with open(CosmotopConfig.CONFIG_PATH) as f:
            if f.read() != script['script']:
                updated = True

        if updated:
            if not script['script']:
                os.remove(CosmotopConfig.CONFIG_PATH)
            else:
                with open(CosmotopConfig.CONFIG_PATH, 'w') as f:
                    f.write(script['script'])

            self.print("Configuration updated, will restart...")
            await scrypted_sdk.deviceManager.requestRestart()

    # should only be called on the primary plugin instance
    async def getReadmeMarkdown(self) -> str:
        await self.config_reconciled
        return f"""
# `cosmotop` Configuration

## Available themes

Additional themes can be downloaded from the theme manager page.

<u>System themes</u>:
{'\n'.join(['- ' + theme for theme in self.system_themes])}

<u>Bundled themes</u>:
{'\n'.join(['- ' + theme for theme in self.bundled_themes])}

<u>User themes</u>:
{'\n'.join(['- ' + theme for theme in self.user_themes])}
"""


class DownloaderBase(ScryptedDeviceBase):
    def __init__(self, nativeId: str | None = None):
        super().__init__(nativeId)

    def downloadFile(self, url: str, filename: str):
        try:
            filesPath = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files')
            fullpath = os.path.join(filesPath, filename)
            if os.path.isfile(fullpath):
                return fullpath
            tmp = fullpath + '.tmp'
            self.print("Creating directory for", tmp)
            os.makedirs(os.path.dirname(fullpath), exist_ok=True)
            self.print("Downloading", url)
            response = urllib.request.urlopen(url)
            if response.getcode() is not None and response.getcode() < 200 or response.getcode() >= 300:
                raise Exception(f"Error downloading")
            read = 0
            with open(tmp, "wb") as f:
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    read += len(data)
                    self.print("Downloaded", read, "bytes")
                    f.write(data)
            os.rename(tmp, fullpath)
            return fullpath
        except:
            self.print("Error downloading", url)
            import traceback
            traceback.print_exc()
            raise


class CosmotopThemeManager(DownloaderBase, Settings, Readme):
    LOCAL_THEME_DIR = os.path.expanduser(f'~/.config/cosmotop/themes')

    def __init__(self, nativeId: str, parent: CosmotopPlugin) -> None:
        super().__init__(nativeId)
        self.parent = parent
        self.cluster_parent_thememanager = asyncio.ensure_future(parent.cluster_parent.getDevice("thememanager")) if parent.cluster_parent else None
        self.themes_loaded = asyncio.ensure_future(self.load_themes())

    # can be called from forks
    async def load_themes(self) -> None:
        self.print("Using themes dir:", CosmotopThemeManager.LOCAL_THEME_DIR)
        os.makedirs(CosmotopThemeManager.LOCAL_THEME_DIR, exist_ok=True)
        try:
            urls = await self.theme_urls()
            for url in urls:
                filename = url.split('/')[-1]
                fullpath = self.downloadFile(url, filename)
                target = os.path.join(CosmotopThemeManager.LOCAL_THEME_DIR, filename)
                shutil.copyfile(fullpath, target)
                self.print("Installed", target)
        except:
            import traceback
            traceback.print_exc()

    # can be called from forks
    async def theme_urls(self) -> list[str]:
        if self.cluster_parent_thememanager is not None:
            return await (await self.cluster_parent_thememanager).theme_urls()
        if self.storage:
            urls = self.storage.getItem('theme_urls')
            if urls:
                return json.loads(urls)
        return []

    # should only be called on the primary plugin instance
    async def getSettings(self) -> list[Setting]:
        return [
            {
                "key": "theme_urls",
                "title": "Theme URLs",
                "description": f"List of URLs to download themes from. Themes will be downloaded to {CosmotopThemeManager.LOCAL_THEME_DIR}.",
                "value": await self.theme_urls(),
                "multiple": True,
            },
        ]

    # should only be called on the primary plugin instance
    async def putSetting(self, key: str, value: str, forward=True) -> None:
        self.storage.setItem(key, json.dumps(value))
        await self.onDeviceEvent(ScryptedInterface.Settings.value, None)

        self.print("Themes updated, will restart...")
        await scrypted_sdk.deviceManager.requestRestart()

    # should only be called on the primary plugin instance
    async def getReadmeMarkdown(self) -> str:
        return f"""
# Theme Manager

List themes to download and install in the local theme directory. Themes will be installed to `{CosmotopThemeManager.LOCAL_THEME_DIR}`.
"""


def create_scrypted_plugin():
    return CosmotopPlugin()


class CosmotopForkEntry:
    async def newCosmotopPlugin(self, nativeId: str = None, cluster_parent: CosmotopPlugin = None):
        return CosmotopPlugin(nativeId=nativeId, cluster_parent=cluster_parent)


async def fork():
    return CosmotopForkEntry()