# Raspberry Pi AI Camera (IMX500) -> ROS 2 Humble container

Status: **blocked at step 1** — the host is not detecting a camera.

## Step 1 (host): make the Pi see the sensor

Nothing in the container can help until this passes. Verified from inside the
container by reading the host's `/sys` on 2026-08-26:

| Present | Meaning |
|---|---|
| `1000880000.pisp_be` (video20–37) | PiSP ISP backend — exists on every Pi 5 |
| `1000800000.codec` (video19, `rpivid`) | HEVC decoder — exists on every Pi 5 |

| Absent | Meaning |
|---|---|
| `rp1-cfe` / `csi` node on the RP1 | CSI front-end never got enabled |
| any `imx*` / `ov*` i2c device or bound driver | no sensor probed |
| camera i2c bus (only `i2c-1`, `i2c-13`, `i2c-14`) | CSI connector i2c not enabled |
| `/dev/video*`, `/dev/media*` in container | nothing passed through yet |

So the ISP is there and idle; there is no sensor behind it.

Run **on the host**, not in here:

```bash
sudo apt update && sudo apt install imx500-all   # sensor firmware blobs
sudo reboot
rpicam-hello --list-cameras                      # the real test
dmesg | grep -iE 'imx500|cfe|csi'
grep -E 'camera|dtoverlay' /boot/firmware/config.txt
```

The AI Camera will not come up without its firmware package installed, so do
that before suspecting the hardware. After that, in order of likelihood:
ribbon seated backwards or in the wrong connector (the Pi 5 has two, `CAM0`
and `CAM1`), or `camera_auto_detect=0` in `/boot/firmware/config.txt`.

Ignore `vcgencmd get_camera` entirely -- it queries the legacy stack, which
does not exist on a Pi 5, and reports `supported=0` no matter what is plugged
in. On this machine it cannot even reach the firmware from inside the
container (`VCHI initialization failed`), because `/dev/vcio` is not passed
through.

## Step 2 (this file): pass the nodes through

Once `--list-cameras` lists the sensor, `/dev/video*` and `/dev/media*` will
appear on the host and get added to `runArgs` as `--device=` lines, plus:

```
--device=/dev/dma_heap/linux,cma
--device=/dev/dma_heap/system
```

libcamera allocates frame buffers from `dma_heap`; without it you get a
camera that enumerates and then fails on first capture.

Do **not** add these lines before the devices exist — Docker refuses to start
a container whose `--device` path is missing, same trap as the serial nodes.

## Step 3: build libcamera + camera_ros in the container

Following the tutorial author's updated Pi 5 instructions, with three changes
for this setup. `v4l2_camera` is NOT usable -- it expects a device that emits
finished frames, and the Pi 5 CSI front-end emits packed raw Bayer that only
libcamera can push through the PiSP ISP. Ubuntu 22.04's packaged libcamera is
a June 2020 snapshot (`0~git20200629`), so it must be built from source.

### Change 1: meson from pip, not apt

The author is on Ubuntu 24.04 (meson 1.3). Jammy's candidate is **0.61.2**,
below libcamera's required >= 0.63, so the build fails at configure time.

    sudo apt install -y ninja-build python3-pip
    pip3 install --user "meson>=1.0"
    export PATH="$HOME/.local/bin:$PATH"     # persist in ~/.bashrc

Adjusted dependency line (meson removed on purpose):

    sudo apt install -y libboost-dev libgnutls28-dev openssl libtiff-dev \
      pybind11-dev qtbase5-dev libqt5core5a cmake python3-yaml python3-ply \
      libglib2.0-dev libgstreamer-plugins-base1.0-dev python3-colcon-meson \
      ninja-build

Unverified risk: we have gcc 11, the author has gcc 13. A compile error
*inside* libcamera (as opposed to a configure error) points here first.

### Change 2: /workspace/camera_ws -- NOT ~ and NOT ros2_ws/src

Two traps here.

`~/camera_ws` is wrong: /home/ros is not a separate mount, so it lives in the
container's writable overlay and is destroyed on every rebuild. Only
/workspace (bind mount to the host) and /opt/claude survive. Check with
`findmnt <path>` if unsure.

`ros2_ws/src` is also wrong: packages there are committed (see rplidar_ros)
and .gitignore only covers build/install/log, so libcamera would land in this
repo's history. Keeping it out also keeps `colcon build` quick when editing
xacros.

So: /workspace/camera_ws, with camera_ws/ added to .gitignore. Source both
workspaces when running.

    mkdir -p /workspace/camera_ws/src
    cd /workspace/camera_ws/src
    git clone https://github.com/raspberrypi/libcamera.git
    git clone https://github.com/christianrauch/camera_ros.git
    cd /workspace/camera_ws
    rosdep install -y --from-paths src --ignore-src \
      --rosdistro $ROS_DISTRO --skip-keys=libcamera
    colcon build --symlink-install

`--skip-keys=libcamera` matters: without it rosdep pulls Ubuntu's 2020
libcamera and you end up linking against the wrong one.

### Change 3: device passthrough (container only)

The author runs on bare metal. Once the host sees the sensor, add to runArgs:

    "--device=/dev/dma_heap/linux,cma",
    "--device=/dev/dma_heap/system",
    plus each /dev/video* and /dev/media* node the camera creates

libcamera allocates frame buffers from dma_heap; without it the camera
enumerates and then fails on first capture.

### Phase 2: move the deps into the image, once this works

After a container rebuild, camera_ws/install still holds the built libcamera
(it is on the bind mount), but the apt packages it links against are gone with
the overlay -- so camera_node fails at load time and the apt line has to be
re-run every rebuild.

Once the build is proven, move the apt dependency list into ros2-humble-dev
(0.3.0). That is the part that hurts to lose. Baking libcamera itself is
optional polish, ~20-40 min of Pi 5 build time saved per machine.

Do NOT bake first: a full image rebuild per iteration is a bad loop while the
gcc 11 question is still open.

### Run

    source /workspace/camera_ws/install/setup.bash
    ros2 run camera_ros camera_node

## What this does NOT give you

`camera_ros` publishes `sensor_msgs/Image`. The IMX500's on-sensor neural
network output arrives as libcamera *frame metadata*, which camera_ros does
not expose -- so this yields a working camera, not a working AI camera.

The author tested with a Camera Module 3, a plain sensor, so the gap does not
show up in the tutorial. Getting detections is separate work on top: either
patch camera_ros to surface the metadata, or run the IMX500 pipeline on the
host with picamera2 and bridge results over a socket.

Images first regardless -- it is the right milestone either way.
