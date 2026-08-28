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

Write to the same media the Pi booted from in Phase 0.

---

## Phase 2 [HOST] -- base setup

First boot, then:

    sudo apt update && sudo apt full-upgrade -y
    sudo reboot

Docker:

    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker mike

Log out and back in (group membership only applies to new sessions), then:

    docker run --rm hello-world

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

**Arduino / motor controller** -- this rule was never tracked. Restore it
from `/tmp/pi-backup.tgz` if you have it. Otherwise recreate it; the CH340
adapter is `1a86:7523`:

    sudo tee /etc/udev/rules.d/99-arduino.rules >/dev/null <<'EOF'
    KERNEL=="ttyUSB*", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", MODE:="0777", SYMLINK+="arduino", SYMLINK+="motor"
    EOF

Both symlinks point at the same physical device -- two nodes, one CH340. The
mode is belt-and-braces given the container uses `--group-add=dialout`.

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

    cd ~ && tar xzf /tmp/pi-backup.tgz .claude .local/share/claude .local/share/mybot

If the backup is missing, reinstall Claude Code on the host and recreate the
shim before the `docker run` below -- the `-v` lines fail without those paths.

Then start the container. Take the `--device` lines for the camera from what
you recorded in Phase 3, and **add nothing that does not exist**:

    docker run -d --name mybot-pi \
      --network=host --ipc=host \
      --device=/dev/arduino --device=/dev/motor --device=/dev/rplidar \
      --device=/dev/ttyUSB0 --device=/dev/ttyUSB1 \
      --device=/dev/dma_heap/linux,cma \
      --device=/dev/dma_heap/system \
      --device=/dev/video0 --device=/dev/media0 \
      --group-add=dialout \
      -v /home/mike/robotics/my_bot:/workspace \
      -v /home/mike/.local/share/claude:/opt/claude:ro \
      -v /home/mike/.claude:/home/ros/.claude \
      -v /home/mike/.local/share/mybot/claude-shim:/usr/local/bin/claude:ro \
      -w /workspace -u ros \
      ghcr.io/mjwoolley/ros2-humble-dev:0.2.0 sleep infinity

    docker exec -it mybot-pi bash

The `dma_heap` nodes are not optional: libcamera allocates frame buffers
there, and without them the camera enumerates and then fails on first
capture. The `video*`/`media*` list above is a placeholder -- use the real
node names from Phase 3, there are usually several.

---

## Phase 6 [CONTAINER] -- rebuild libcamera + camera_ros

    bash .devcontainer/pi/setup-camera-ws.sh

Roughly 5-15 minutes on a Pi 5. See RUNBOOK-camera.md for what the script
does and what the failure modes look like.

Verify it linked against the right libcamera -- this is the failure that
looks like success:

    source /workspace/camera_ws/install/setup.bash
    ldd $(ros2 pkg prefix camera_ros)/lib/camera_ros/camera_node | grep -i libcamera

Both entries must resolve into `/workspace/camera_ws/install`. Anything in
`/usr/lib/aarch64-linux-gnu` means it picked up the distro's June-2020
libcamera and the build has to be redone.

Then the actual goal:

    ros2 run camera_ros camera_node
    # [WORKSTATION] ros2 topic list ; rqt_image_view

---

## Phase 7 -- write the working config back into the repo

Once frames are publishing, put the camera `--device` lines into
`.devcontainer/pi/devcontainer.json` `runArgs` so the devcontainer flow
matches the hand-rolled `docker run`, and update README-camera.md's status
line. Commit both, plus the arduino udev rule if you recreated it -- losing
it twice would be careless.

---

## Verification checklist

- [ ] `id mike` -> uid 1000, dialout present
- [ ] `docker run --rm hello-world` works without sudo
- [ ] `rpicam-hello --list-cameras` lists the IMX500
- [ ] `/dev/arduino`, `/dev/motor`, `/dev/rplidar` all resolve
- [ ] container starts with every `--device` line present
- [ ] `ldd` on camera_node points into `/workspace/camera_ws/install`
- [ ] `ros2 run camera_ros camera_node` publishes `sensor_msgs/Image`
- [ ] lidar and motors still work: see the launch commands in devcontainer.json

## What this still does not give you

`camera_ros` publishes images. The IMX500's on-sensor neural network output
arrives as libcamera frame metadata, which camera_ros does not expose. Moving
to Raspberry Pi OS makes that reachable -- picamera2 with the IMX500 pipeline
is packaged there -- but it remains separate work on top. See the end of
README-camera.md.
