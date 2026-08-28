# Runbook: Pi 5 camera -> ROS 2 Humble (terminal, no VS Code)

Goal: `ros2 run camera_ros camera_node` publishing `sensor_msgs/Image` inside
the Pi devcontainer.

Background and reasoning live in `README-camera.md`. This file is just the
steps.

Every command is tagged **[HOST]** (a shell on the Pi itself) or
**[CONTAINER]** (a shell inside the devcontainer). Getting these mixed up is
the main way to waste an hour here.

Not sure which you are in?

    ls /.dockerenv >/dev/null 2>&1 && echo CONTAINER || echo HOST

---

## Step 0 [HOST] — recreate the container from the base image

An earlier `apt install libraspberrypi-bin v4l-utils ros-humble-v4l2-camera`
pulled several hundred packages into the container (full OpenCV, VTK, GDAL,
GTK3, Qt, systemd). None of it is wanted. It all lives in the container's
writable overlay, so recreating the container discards it -- there is nothing
to uninstall.

Nothing in `/workspace` is at risk: it is a bind mount to
`/home/mike/robotics/my_bot` on the host. `/home/ros` inside the container is
NOT preserved.

### Option A: devcontainer CLI (preferred -- reads devcontainer.json, no drift)

    npm install -g @devcontainers/cli        # once

    cd /home/mike/robotics/my_bot
    devcontainer up \
      --workspace-folder . \
      --config .devcontainer/pi/devcontainer.json \
      --remove-existing-container

Then get a shell in it:

    devcontainer exec \
      --workspace-folder . \
      --config .devcontainer/pi/devcontainer.json \
      bash

### Option B: plain docker

Find and remove the running container:

    docker ps                      # note the NAME of the ros2-humble-dev one
    docker rm -f <name>

Recreate it (this mirrors the current devcontainer.json plus the mounts
observed on the running container):

    docker run -d --name mybot-pi \
      --network=host --ipc=host \
      --device=/dev/arduino --device=/dev/motor --device=/dev/rplidar \
      --device=/dev/ttyUSB0 --device=/dev/ttyUSB1 \
      --group-add=dialout \
      -v /home/mike/robotics/my_bot:/workspace \
      -v /home/mike/.local/share/claude:/opt/claude:ro \
      -v /home/mike/.claude:/home/ros/.claude \
      -v /home/mike/.local/share/mybot/claude-shim:/usr/local/bin/claude:ro \
      -w /workspace -u ros \
      ghcr.io/mjwoolley/ros2-humble-dev:0.2.0 sleep infinity

    docker exec -it mybot-pi bash

All five `--device` paths must exist on the host or docker refuses to start.
If something is unplugged, drop its line for the session.

Confirm you are back on a clean base:

    [CONTAINER] dpkg -l | grep -c libopencv        # expect 0
    [CONTAINER] which vcgencmd                     # expect nothing

---

## Step 1 [CONTAINER] — build libcamera + camera_ros

Does not need the camera attached, so start it now and do Step 2 while it
runs. Roughly 20-40 minutes on a Pi 5.

    bash .devcontainer/pi/setup-camera-ws.sh

The script installs the apt dependencies, gets meson from pip (Jammy's apt
meson is 0.61.2; libcamera needs >= 0.63), clones both repos into
`/workspace/camera_ws`, and builds.

`/workspace/camera_ws` is on the bind mount, so the build survives container
rebuilds. The apt dependencies do not -- see "Phase 2" in README-camera.md.

If it fails *inside* libcamera's own C++ (not at the meson configure step),
suspect gcc 11 -- the tutorial author is on Ubuntu 24.04 with gcc 13. Report
the error rather than guessing.

---

## Step 2 [HOST] — make the Pi detect the sensor

The AI Camera needs firmware blobs uploaded to the sensor at runtime and will
not enumerate without them.

    sudo apt update
    sudo apt install imx500-all       # verify name against current RPi docs
    sudo reboot

After the reboot:

    rpicam-hello --list-cameras

That is the authoritative test. Expect the sensor listed with its modes.

Ignore `vcgencmd get_camera` completely. It queries the legacy camera stack,
which does not exist on a Pi 5, and reports `supported=0` regardless. Inside
the container it cannot even reach the firmware (`VCHI initialization
failed`) because `/dev/vcio` is not passed through. It is not a camera fault.

If `--list-cameras` finds nothing:

    dmesg | grep -iE 'imx500|cfe|csi'
    grep -E 'camera|dtoverlay' /boot/firmware/config.txt

Likely causes in order: firmware package missing (do the apt step first),
ribbon seated backwards or in the wrong connector (the Pi 5 has two, CAM0 and
CAM1), or `camera_auto_detect=0` in `/boot/firmware/config.txt`.

---

## Step 3 [HOST] — list the device nodes, then pass them through

Once `--list-cameras` works:

    ls -l /dev/video* /dev/media* /dev/dma_heap/*

Add to `runArgs` in `.devcontainer/pi/devcontainer.json` a `--device=` line
for each `/dev/video*` and `/dev/media*` node the camera created, plus:

    "--device=/dev/dma_heap/linux,cma",
    "--device=/dev/dma_heap/system",

The dma_heap entries are not optional -- libcamera allocates frame buffers
there, and without them the camera enumerates and then fails on first
capture.

Recreate the container (Step 0) for the new devices to appear.

Do NOT add these lines before the nodes exist on the host: docker refuses to
start a container with a missing `--device` path.

---

## Step 4 [CONTAINER] — run it

    source /opt/ros/humble/setup.bash
    source /workspace/ros2_ws/install/setup.bash
    source /workspace/camera_ws/install/setup.bash

    ros2 run camera_ros camera_node

Check from another container shell:

    ros2 topic list
    ros2 topic hz /camera/image_raw

"No cameras available" here, after `rpicam-hello --list-cameras` works on the
host, means the passthrough in Step 3 is incomplete.

---

## Known limitation

`camera_ros` publishes images only. The IMX500's on-sensor neural network
results come back as libcamera frame metadata, which `camera_ros` does not
expose -- so this gives you a working camera, not a working AI camera. The
tutorial author used a Camera Module 3, a plain sensor, so the gap does not
appear there. Deferred by choice; see README-camera.md.
