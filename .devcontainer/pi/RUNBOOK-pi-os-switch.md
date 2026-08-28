# Runbook: switch the Pi host from Ubuntu 24.04 to Raspberry Pi OS

Goal: a Pi host whose kernel can see the AI Camera (IMX500), with the ROS
devcontainer and serial hardware working exactly as before.

## Why

Verified on 2026-08-27, from inside the container, against the running host:

    /proc/version -> Linux 6.8.0-1061-raspi ... Ubuntu 13.3.0 ... 24.04.1
    /boot/firmware/overlays/  -> imx219 imx258 imx290 imx296 imx327 imx378
                                 imx462 imx477 imx519 imx708 ov5647
                                 ... and no imx500
    /lib/modules/6.8.0-1061-raspi/kernel/drivers/media/i2c/ -> 16 imx*.ko, no imx500
    apt-cache search imx500   -> nothing
    ls /lib/firmware | grep imx500 -> nothing

Ubuntu's `-raspi` kernel is Canonical's, tracking a subset of Raspberry Pi's
tree. It supports every mainstream Pi camera except the newest one. The
IMX500 driver, its `imx500.dtbo` overlay and its firmware blobs live only in
Raspberry Pi's kernel tree and apt archive -- i.e. only on Raspberry Pi OS.

The switch is cheap because ROS runs in a container: `ros2-humble-dev` is
Ubuntu 22.04 whatever the host is. The host only has to boot the board, run
Docker, own the kernel drivers and own the udev rules. Raspberry Pi OS is
better at the third of those and no worse at the rest.

**The container build in RUNBOOK-camera.md does not change.** The Ubuntu 22.04
container still cannot use Raspberry Pi OS's libcamera packages, so libcamera
+ camera_ros still get built from source inside it. This switch unblocks
Step 1 (host sees the sensor) and leaves Step 3 alone.

Commands are tagged **[OLD HOST]**, **[HOST]** (the reflashed Pi),
**[CONTAINER]**, or **[WORKSTATION]**.

    ls /.dockerenv >/dev/null 2>&1 && echo CONTAINER || echo HOST

---

## Phase 0 [OLD HOST] -- back up what is not in git

Do this before touching the boot media. The repo itself is safe (it is on
GitHub), but several things this setup depends on exist only on the Pi.

**Not in git, and needed again:**

| Thing | Path |
|---|---|
| arduino/motor udev rule | `/etc/udev/rules.d/*` (rule name unknown -- look) |
| Claude Code CLI + shim | `~/.local/share/claude`, `~/.local/share/mybot/claude-shim`, `~/.claude` |
| SSH host + user keys | `~/.ssh` |
| boot config | `/boot/firmware/config.txt` |
| WiFi credentials | NetworkManager connections |

**Not worth backing up:** `camera_ws` (103 MB, gitignored, rebuilds in
~5 minutes now that the jinja2 dependency is fixed).

Record the current device wiring first -- you will want to compare after:

    ls -l /dev/serial/by-id/
    lsusb
    ls /etc/udev/rules.d/
    cat /boot/firmware/config.txt

Then tar the lot:

    cd ~
    tar czf /tmp/pi-backup.tgz \
      .ssh .claude .local/share/claude .local/share/mybot \
      /etc/udev/rules.d /boot/firmware/config.txt 2>/dev/null

Copy it OFF the Pi -- [WORKSTATION]:

    scp mike@raspberrypi:/tmp/pi-backup.tgz ~/pi-backup.tgz

Also confirm any uncommitted work is pushed -- [OLD HOST]:

    cd ~/robotics/my_bot && git status && git push

Finally, note **which media the Pi boots from** (SD, USB or NVMe):

    lsblk

---

## Phase 1 [WORKSTATION] -- flash Raspberry Pi OS

Use Raspberry Pi Imager. Choose **Raspberry Pi OS (64-bit)** -- the current
stable release. 64-bit is required: the `ros2-humble-dev` image is arm64.

In Imager's OS customisation screen, set these to match the old host, or
paths and permissions break later:

| Setting | Value | Why |
|---|---|---|
| Hostname | `raspberrypi` | keeps existing SSH config and DDS habits working |
| Username | `mike` | first user gets uid 1000; the container's `ros` is uid 1000, which is what makes the `/workspace` bind mount writable |
| SSH | enable, public-key auth | paste your workstation's `~/.ssh/id_*.pub` |
| WiFi / locale | as before | |

Also set a password even with key-only SSH -- everything from Phase 2 on uses
`sudo`, and key auth does not help at a sudo prompt.

Write to the same media the Pi booted from in Phase 0 (a **microSD** card, as
of 2026-08-28 -- note it presents as a USB device when read through a card
reader, which is not the same thing).

### Imager customisation does not work on Trixie

Pi OS Trixie (2026-06-18 image onward) takes its first-boot config from
**cloud-init**, not Imager's `firstrun.sh`. `rpi-imager` 1.8.5 writes the old
mechanism, the image ignores it, and you get a stock install with no user, no
SSH and no WiFi -- with no error anywhere. Verify before booting: if
`bootfs` has no `firstrun.sh` or `custom.toml`, and `cmdline.txt` has no
`systemd.run=` token, nothing was applied.

Either upgrade Imager, or write the cloud-init files directly to `bootfs`
after flashing. `user-data` (`#cloud-config`) with the first `users:` entry
named `mike` is the important one: the `raspberry_pi_os` cloud-init distro
class routes the first user through `/usr/lib/userconf-pi/userconf`, which
**renames** the stock `pi` account instead of adding a second one -- so
`mike` inherits uid 1000. A naive user block gives you `mike` at 1001 with
`pi` still holding 1000, and the `/workspace` bind mount breaks.

`network-config` is netplan v2 and accepts the raw 64-hex PSK directly, so
WiFi can be restored from the old install's `network-config` without knowing
the passphrase. Also `touch bootfs/ssh` -- `sshswitch.service` enables sshd
only if that file exists.

### Two host settings that are not in any config file

**WiFi ships disabled.** `nmcli radio wifi` reports `disabled` and
`/sys/class/rfkill/*/soft` is `1` for `phy0`. Netplan renders the config
correctly and the interface still never comes up. Fix: `sudo nmcli radio
wifi on`.

**HDMI on a monitor with no EDID.** If the display shows the boot screen then
goes black, or reports "Out of Range":

    # /boot/firmware/cmdline.txt -- ONE line, no trailing newline
    video=HDMI-A-1:e drm.edid_firmware=HDMI-A-1:edid/forced-1080p.bin

`e` forces the connector connected regardless of hotplug detect. Do **not**
spell out `1920x1080@60` in that token: with no EDID the kernel computes GTF
timings at ~172.8 MHz and the monitor reports "Out of Range". The synthetic
EDID supplies the standard CEA-861 timing at 148.5 MHz instead. Generate and
install it with `f.sh` from the Phase 0 backup, which writes
`/lib/firmware/edid/forced-1080p.bin`.

vc4 is a module inside the initramfs, so the first six firmware-load attempts
fail with `-2` before the rootfs is mounted; the retry after mount succeeds.
Those errors are expected. The console stays at 1024x768 during early boot
and the desktop comes up at 1080p.

Legacy `hdmi_group` / `hdmi_mode` / `hdmi_safe` / `hdmi_force_hotplug` are
**ignored on Pi 5 under full KMS**. Do not reach for them.

**A supply with no USB-C PD negotiation** (bench regulator, USB-to-bare-wire)
makes the firmware assume 900 mA and warn on screen. Two separate settings,
both needed:

    # /boot/firmware/config.txt, under [all]
    usb_max_current_enable=1        # lifts the USB current cap

    sudo rpi-eeprom-config --apply <(rpi-eeprom-config; echo PSU_MAX_CURRENT=5000)

The config.txt line alone does not clear the warning -- that message comes
from the bootloader before Linux starts, so it needs the EEPROM setting.
Confirm with `cat /proc/device-tree/chosen/power/max_current | od -An -tu4
--endian=big` (expect 5000, not 900). Note `rpi-eeprom-config --apply` also
flashes the packaged bootloader image, which may be newer than the installed
one.

---

## Phase 2 [HOST] -- base setup

First boot, then:

    sudo apt update && sudo apt full-upgrade -y
    sudo reboot

**Check the apt indexes before installing anything.** The 2026-06-18 Pi OS
image shipped with every `main` component `Packages` file truncated to zero
bytes, while `contrib`/`non-free` were fine. apt knew 6,257 packages instead
of ~165,000, and `apt-get update` reported `Hit` every time because the
`InRelease` files were valid -- so it never refetched them.

    apt-cache stats | head -2      # expect ~165,000 package names

Anything near 6,000 means the indexes are broken. The symptom downstream is
baffling: `docker-ce Depends iptables but none of the choices are
installable: [no choices]` -- not a version conflict, apt simply has no
record that `iptables` exists. Fix:

    sudo rm -rf /var/lib/apt/lists/*
    sudo apt-get update

Docker:

    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker mike

Log out and back in (group membership only applies to new sessions), then:

    docker run --rm hello-world

The `usermod` fails the first time you run these together -- the `docker`
group does not exist until `docker-ce` is installed. Run it after, not
alongside. And `permission denied ... /var/run/docker.sock` afterwards means
your shell predates the `usermod`; open a new one.

Confirm the base facts this setup assumes:

    id mike                 # expect uid=1000, and dialout among the groups
    getent group dialout    # expect gid 20, matching the container

---

## Phase 3 [HOST] -- the camera

This is the whole point of the exercise, so do it before rebuilding the rest.

    sudo apt update
    sudo apt install imx500-all      # verify the name against current RPi docs
    sudo reboot

The AI Camera uploads firmware to the sensor at runtime and will not
enumerate without those blobs. After the reboot:

    rpicam-hello --list-cameras

That is the real test. It should list the IMX500. Also confirm the pieces
Ubuntu was missing are now present:

    ls /boot/firmware/overlays/ | grep -i imx500     # expect imx500.dtbo
    ls /lib/firmware/ | grep -i imx500               # expect firmware blobs
    dmesg | grep -iE 'imx500|cfe|csi'

**Write down the device nodes** -- Phase 5 needs them:

    ls -l /dev/video* /dev/media* /dev/dma_heap/

If `--list-cameras` finds nothing, do not proceed to passthrough. In order of
likelihood: ribbon seated backwards, wrong connector (the Pi 5 has `CAM0` and
`CAM1`), or `camera_auto_detect=0` in `/boot/firmware/config.txt`. Ignore
`vcgencmd get_camera` entirely -- it queries the legacy stack, which does not
exist on a Pi 5.

---

## Phase 4 [HOST] -- udev rules for the serial devices

Without these, `/dev/arduino`, `/dev/motor` and `/dev/rplidar` do not exist
and the container refuses to start (Docker will not start a container whose
`--device` path is missing).

Clone the repo first:

    mkdir -p ~/robotics && cd ~/robotics
    git clone https://github.com/mjwoolley/my_bot.git
    cd my_bot

**RPLIDAR** (rule is tracked in the repo):

    sudo cp ros2_ws/src/rplidar_ros/scripts/rplidar.rules /etc/udev/rules.d/

Do not use `create_udev_rules.sh` -- it calls `colcon_cd`, which needs a
sourced ROS workspace, and ROS only exists inside the container. udev only
runs on the host.

**All three rules are now tracked in this repo** at `.devcontainer/pi/udev/`
(they were not, before 2026-08-28 -- the arduino/motor rules existed only on
the old Pi's filesystem). Install them:

    sudo cp .devcontainer/pi/udev/*.rules /etc/udev/rules.d/
    sudo chown root:root /etc/udev/rules.d/*.rules

Do not hand-type a replacement. An earlier version of this runbook suggested
a single `99-arduino.rules` with `MODE:="0777"`, which is worse than what was
actually running in three ways:

  - `0777` makes the motor controller world-writable. The real rules use
    `MODE="0660", GROUP="dialout"`, which is sufficient because both you and
    the container's `ros` user are in `dialout`.
  - It omitted `ENV{ID_MM_DEVICE_IGNORE}="1"`. Without that, ModemManager
    probes the CH340 with AT commands on every plug-in -- a real cause of
    flaky motor comms on first connect.
  - It matched `KERNEL=="ttyUSB*"` rather than `SUBSYSTEM=="tty"`, so it
    would silently stop working if the board ever enumerated as `ttyACM*`.

(If you do paste a heredoc: the closing `EOF` must be at column 0. Indenting
it leaves you at a `>` continuation prompt with the command hanging.)

Reload and verify:

    sudo udevadm control --reload && sudo udevadm trigger
    ls -l /dev/arduino /dev/motor /dev/rplidar /dev/ttyUSB*

Expect `arduino`, `motor` and `ttyUSB0` on one minor number and `rplidar`
with `ttyUSB1` on the other. Compare against the Phase 0 `ls -l
/dev/serial/by-id/` output. If the vendor IDs do not match what you recorded,
fix the rule rather than the symptoms.

---

## Phase 5 [HOST] -- restore Claude Code and start the container

Restore the shim and config from the backup:

    cd ~ && tar xzf /tmp/pi-backup.tgz .claude .local/share/mybot

`.local/share/claude` is deliberately NOT in the backup -- it is ~1.1 GB of
cached CLI builds and re-downloads in seconds. But the shim resolves
`/opt/claude/versions/*`, so that directory must exist and hold a build:

    curl -fsSL https://claude.ai/install.sh | bash

All three paths must exist before the container starts, or the `-v` lines
create root-owned empty directories instead of failing loudly.

Then start the container:

    bash .devcontainer/pi/start-container.sh
    docker exec -it mybot-pi bash

Use the script rather than a hand-written `docker run`. It generates the
`--device` list from what is actually present -- 37 devices on a working Pi 5
-- and warns about anything missing instead of letting Docker refuse to
start. `.devcontainer/pi/devcontainer.json` carries the same list explicitly
for the VS Code flow.

Three things earlier versions of this runbook got wrong, all of which fail
in ways that look like a working setup:

  - **`--device=/dev/video0 --device=/dev/media0` is not enough, and names
    the wrong device.** `media0` is `pispbe` (the ISP); the camera is behind
    `media2` (`rp1-cfe`). libcamera needs the whole rp1-cfe + pispbe set plus
    the three subdevs. `media3` is the HEVC decoder and is not needed --
    libcamera logs one harmless ERROR line skipping it.
  - **`-v /run/udev:/run/udev:ro` is mandatory.** libcamera enumerates
    through udev. Without it, every device node is present and readable and
    libcamera still reports `no cameras available`, having never looked.
  - **`--group-add=video` is needed**, not just `dialout`. All the
    `video*`/`media*`/`dma_heap` nodes are `root:video`. It happens to work
    without it via the host ACL (`user:mike:rw-`, and `ros` is also uid
    1000), but that ACL is not tracked anywhere.

Also pass `--init`: PID 1 is `sleep infinity`, which never reaps children, so
each stopped `camera_node` leaves a zombie.

The `dma_heap` nodes are not optional either: libcamera allocates frame
buffers there, and without them the camera enumerates and then fails on
first capture.

---

## Phase 6 [CONTAINER] -- rebuild libcamera + camera_ros

    bash .devcontainer/pi/setup-camera-ws.sh

Roughly 5-15 minutes on a Pi 5. See RUNBOOK-camera.md for what the script
does and what the failure modes look like.

Verify it linked against the right libcamera -- this is the failure that
looks like success:

    source /workspace/camera_ws/install/setup.bash
    ldd /workspace/camera_ws/install/camera_ros/lib/libcamera_component.so \
      | grep -i libcamera

**Check `libcamera_component.so`, NOT `camera_node`.** `camera_node` is a
thin executable that only links `rclcpp`; the camera code lives in the
composable-node library. Running `ldd` on `camera_node` prints no libcamera
lines at all, which looks like a clean result and tells you nothing -- it
reports the same empty output whether the build is right or wrong.

Expect all three of these to resolve into `/workspace/camera_ws/install`:

    libcamera.so.0.7       -> .../install/libcamera/lib/
    libcamera-base.so.0.7  -> .../install/libcamera/lib/
    libpisp.so.1           -> .../install/libcamera/lib/

`libpisp` is the strongest signal: it is the Pi 5 ISP support that the
distro's June-2020 libcamera does not have at all. Anything resolving into
`/usr/lib/aarch64-linux-gnu` means the build has to be redone.

Then the actual goal:

    ros2 run camera_ros camera_node
    # [WORKSTATION] ros2 topic list ; rqt_image_view

---

## Phase 7 -- write the working config back into the repo

**Done 2026-08-28.** Now tracked on `main`:

  - `.devcontainer/pi/devcontainer.json` -- full 37-device `runArgs`, the
    `/run/udev` mount, `--group-add=video`, `--init`
  - `.devcontainer/pi/start-container.sh` -- generates the device list from
    what is present; prefer it over the JSON when hardware may be unplugged
  - `.devcontainer/pi/udev/*.rules` -- all three rules, tracked for the first
    time. They previously existed only on the Pi's filesystem, which is
    exactly how they came to be lost
  - `.devcontainer/pi/setup-camera-ws.sh` -- was on `camera-setup` only
  - `camera_ws/` in `.gitignore`

---

## Verification checklist

Verified end-to-end on 2026-08-28 (Pi OS Trixie, image 2026-06-18):

- [x] `id mike` -> uid 1000, dialout present
- [x] `docker run --rm hello-world` works without sudo
- [x] `rpicam-hello --list-cameras` lists the IMX500
- [x] `/dev/arduino`, `/dev/motor`, `/dev/rplidar` all resolve
- [x] container starts with every `--device` line present (37 of them)
- [x] `ldd` on **`libcamera_component.so`** resolves libcamera, libcamera-base
      and libpisp into `/workspace/camera_ws/install`
- [x] `ros2 run camera_ros camera_node` publishes `sensor_msgs/Image` --
      800x600 `bgra8`, sustained 30.0 Hz on `/camera/image_raw`
- [ ] lidar and motors still work: see the launch commands in devcontainer.json
      -- **not yet re-tested after the OS switch**

Known gap: `/camera/camera_info` publishes `height: 0, width: 0` with no
distortion model. The camera is uncalibrated, which is fine for viewing
frames but not for anything needing intrinsics (AprilTags, visual odometry).

## What this still does not give you

`camera_ros` publishes images. The IMX500's on-sensor neural network output
arrives as libcamera frame metadata, which camera_ros does not expose. Moving
to Raspberry Pi OS makes that reachable -- picamera2 with the IMX500 pipeline
is packaged there -- but it remains separate work on top. See the end of
README-camera.md.
