import asyncio
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
COSMOTOP_DOWNLOAD = f"https://github.com/bjia56/cosmotop/releases/download/{COSMOTOP_VERSION}/cosmotop.exe"
DOWNLOAD_CACHE_BUST = f"{platform.system()}-{platform.machine()}-{COSMOTOP_VERSION}-1"

APE_ARM64 = "https://cosmo.zip/pub/cosmos/bin/ape-arm64.elf"
APE_X86_64 = "https://cosmo.zip/pub/cosmos/bin/ape-x86_64.elf"
APE_MACOS_X86_64 = "https://cosmo.zip/pub/cosmos/bin/ape-x86_64.macho"

FILES_PATH = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files')
CACHEBUST_PATH = os.path.join(FILES_PATH, 'cachebust')

# fill this in with the system APE loader
COMPAT_SCRIPT = """#!/bin/sh
SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
exec $SCRIPTPATH/{} $SCRIPTPATH/cosmotop.exe "$@"
"""

COMPAT_SCRIPT_MAC_ARM64 = """#!/bin/sh
SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
exec $SCRIPTPATH/cosmotop.exe "$@"
"""


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

        self.config = CosmotopConfig("config", self)
        self.thememanager = CosmotopThemeManager("thememanager", self)

    async def do_download(self) -> None:
        self.exe = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files', 'cosmotop.exe')
        self.compat_exe = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files', 'cosmotop')

        if not self.shouldDownloadCosmotop():
            return

        shutil.rmtree(FILES_PATH, ignore_errors=True)
        self.downloadFile(COSMOTOP_DOWNLOAD, 'cosmotop.exe')

        if platform.system() != 'Windows':
            download = None
            if platform.system() == 'Linux':
                if platform.machine() == 'aarch64':
                    download = APE_ARM64
                elif platform.machine() == 'x86_64':
                    download = APE_X86_64
            elif platform.system() == 'Darwin':
                if platform.machine() == 'x86_64':
                    download = APE_MACOS_X86_64

            if download:
                filename = os.path.basename(download)
                self.downloadFile(download, filename)
                os.chmod(os.path.join(FILES_PATH, filename), 0o755)
                self.ape = os.path.join(FILES_PATH, filename)
            else:
                self.ape = None

            os.chmod(self.exe, 0o755)

            if self.ape:
                with open(self.compat_exe, 'w') as f:
                    f.write(COMPAT_SCRIPT.format(os.path.basename(self.ape)))
                os.chmod(self.compat_exe, 0o755)
            else:
                with open(self.compat_exe, 'w') as f:
                    f.write(COMPAT_SCRIPT_MAC_ARM64)
                os.chmod(self.compat_exe, 0o755)
        else:
            self.ape = None
            self.compat_exe = self.exe

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
                devices.append({
                    "nativeId": worker_id,
                    "name": "cosmotop on " + worker['name'],
                    "type": ScryptedDeviceType.API.value,
                    "interfaces": [
                        ScryptedInterface.StreamService.value,
                        ScryptedInterface.TTY.value,
                        ScryptedInterface.Settings.value,
                    ],
                })

        for device in devices:
            await scrypted_sdk.deviceManager.onDeviceDiscovered(device)

        if scrypted_sdk.clusterManager:
            for worker_id in list(workers.keys()):
                worker = workers[worker_id]
                if worker['mode'] == 'server':
                    continue
                fork = scrypted_sdk.fork({ 'clusterWorkerId': worker_id })
                result = await fork.result
                self.cluster_workers[worker_id] = await result.newCosmotopPlugin(worker_id, self)

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
            termsvc = await termsvc.forkInterface(ScryptedInterface.StreamService.value, { 'clusterWorkerId': self.nativeId })
        else:
            termsvc = await scrypted_sdk.sdk.connectRPCObject(termsvc)
        return await termsvc.connectStream(input, {
            'cmd': [self.compat_exe, '--utf-force'],
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
                "title": "cosmotop.exe Path",
                "description": "Path to the downloaded cosmotop.exe.",
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
        cosmotop = self.parent.compat_exe
        assert cosmotop is not None

        child = await asyncio.create_subprocess_exec(cosmotop, '--show-defaults', stdout=asyncio.subprocess.PIPE)
        stdout, _ = await child.communicate()

        return stdout.decode()

    # can be called from forks
    async def reconcile_from_disk(self) -> None:
        await self.parent.downloaded
        await self.parent.thememanager.themes_loaded

        try:
            cosmotop = self.parent.compat_exe
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
                child = await asyncio.create_subprocess_exec(cosmotop, '--show-themes', stdout=asyncio.subprocess.PIPE)
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