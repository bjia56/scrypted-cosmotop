# cosmotop

This plugin downloads and installs the [`cosmotop`](https://github.com/bjia56/cosmotop) monitoring tool, making it available for use inside Scrypted.

## Setup and customization

Out of the box, `cosmotop` is able to view CPU, memory, disk, network, and process activity in your Scrypted installation environment.

### Configuration

The Configuration device under this plugin provides a handy way to view and edit the configuration file for `cosmotop`, typically stored on disk at `~/.config/cosmotop/cosmotop.conf`. This file is kept up to date by Scrypted and will be included in Scrypted system backups.

### GPU monitoring

Monitoring of GPUs is supported on Linux and Windows.
- Windows: LibreHardwareMonitor is included with `cosmotop` and automatically used to fetch GPU information.
- Linux: Intel, AMD, and NVIDIA GPUs are supported, provided the appropriate driver is installed, and the following:
  - Intel: The simplest setup is to run Scrypted as root (if using a local install) or in a privileged container (if using Docker or LXC), however this is <u>*insecure and not recommended*</u>. Instead, run [intel-gpu-exporter](https://github.com/bjia56/intel-gpu-exporter) in a privileged Docker container and use the Prometheus monitoring instructions below.
  - AMD: Should work out of the box.
  - NVIDIA: `libnvidia-ml.so` must be accessible by Scrypted.

### NPU monitoring

Utilization monitoring of Intel and Rockchip NPUs is supported on Linux, provided the following:
- Intel: The path `/sys/devices/pci0000:00/0000:00:0b.0` must be readable by Scrypted.
- Rockchip: The path `/sys/kernel/debug/rknpu` must be readable by Scrypted.

The Apple Neural Engine on M series Macs is supported and detected as an NPU.

### Monitoring via Prometheus ingestion

`cosmotop` supports polling an HTTP Prometheus client endpoint to read GPU and NPU metrics data. The `prometheus_endpoint` configuration key should be set to the HTTP endpoint which publishes the metrics.

Some options for exporters:
- (Linux) [intel-gpu-exporter](https://github.com/bjia56/intel-gpu-exporter): Set the following `cosmotop` configuration keys:
  - `prometheus_mapping`: `"gpu_utilization_percent:igpu_engines_busy_max,gpu_frequency:igpu_frequency_actual,gpu_power_usage:igpu_power_gpu"`
  - `prometheus_settings`: `"gpu_frequency_unit:mhz,gpu_power_unit:w"`
- (MacOS) [macmon-prometheus-exporter](https://github.com/DMontgomery40/macmon-prometheus-exporter): Set the following `cosmotop` configuration keys:
  - `prometheus_mapping`: `"gpu_utilization_percent:mac_gpu_usage_percent,gpu_frequency:mac_gpu_frequency_mhz,gpu_power_usage:mac_gpu_power_watts,gpu_temperature:mac_gpu_temperature_celsius,npu_utilization_percent:mac_ane_usage_percent"`
  - `prometheus_settings`: `"gpu_frequency_unit:mhz,gpu_power_unit:w,gpu_temperature_unit:c"`

### Themes

A number of themes are available within `cosmotop`. A list of available themes is listed under the Configuration device's README. To download additional themes, add URLs under the Theme Manager device.

## Recommended plugins

### `@scrypted/x11-camera`

This plugin allows you to set up a virtual camera that converts `cosmotop` output into a video stream. The stream can then be viewed by external Scrypted integrations like a normal camera, and even be recorded by Scrypted NVR.

To set up, follow the installation instructions under the `@scrypted/x11-camera` README and create a new virtual camera device. Set the executable to `cosmotop` (or `cosmotop.exe` on Windows).
