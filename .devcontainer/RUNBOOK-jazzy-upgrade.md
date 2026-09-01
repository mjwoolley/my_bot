# Runbook: ROS 2 Humble -> Jazzy

Goal: move the container image, this workspace and the simulation from
Humble (Ubuntu 22.04) to Jazzy (Ubuntu 24.04), without losing the Pi 5
camera that took the Pi OS migration to get working.

Status: **planned, not started.** Written 2026-08-28, immediately after
RUNBOOK-pi-os-switch.md completed. Package availability below was checked
against the live ROS 2 apt index for `noble` on that date.

Commands are tagged **[WORKSTATION]**, **[HOST]** (the Pi) or
**[CONTAINER]**.

## Why

Humble is supported to May 2027, so this is not urgent. The reasons to move:

  - Gazebo Classic is EOL (January 2025). It still works, but nothing is
    being fixed in it.
  - Jazzy is supported to May 2029.
  - **`ros-jazzy-desktop-full` is published for arm64.** Humble's is not.
    This is the interesting one -- see "The desktop split may be obsolete".

The reason to wait: everything currently works, and the camera path is
newly-built and lightly tested.

---

## Scope

Two repos change:

| Repo | Change |
|---|---|
| `ros2-humble-dev` | base image, package names, rosdep distro. The repo and image name become wrong -- see naming below. |
| `my_bot` (this one) | `mybot_gazebo` and every `<gazebo>` block in the xacro files; both `devcontainer.json` files |

**Do these as separate commits, and ideally separate sittings.** The base
image bump and the Gazebo migration fail independently, and debugging them
together is miserable.

---

## Prerequisite -- close the open item first

CLOSED. The lidar and motors **have** been driven since the Pi OS switch, so
a failure after the distro jump is no longer ambiguous between the OS switch
and the distro bump. For reference, the commands that exercised them:

    # [CONTAINER]
    ros2 launch rplidar_ros rplidar_c1_launch.py \
      serial_port:=/dev/rplidar frame_id:=laser_frame
    ros2 run serial_motor_demo driver --ros-args \
      -p serial_port:=/dev/motor -p baud_rate:=57600

---

## Phase 1 [WORKSTATION] -- the base image

In `~/robotics/docker/ros2-humble-dev`:

    FROM ros:humble-ros-base      ->  FROM ros:jazzy-ros-base
    ros-humble-*                  ->  ros-jazzy-*
    rosdep update --rosdistro=humble  ->  --rosdistro=jazzy

Verified present for **both arm64 and amd64** on the Jazzy index:
`ros-jazzy-ros-base`, `ros-jazzy-desktop`, `ros-jazzy-desktop-full`,
`ros-jazzy-xacro`, `ros-jazzy-robot-state-publisher`,
`ros-jazzy-joint-state-publisher`, `ros-jazzy-joint-state-publisher-gui`,
`ros-jazzy-rqt`, `ros-jazzy-rqt-common-plugins`.

**`ros-jazzy-gazebo-ros-pkgs` does not exist, for any architecture.** There
is no Classic in Jazzy. `Dockerfile.desktop` must replace it with
`ros-jazzy-ros-gz` (Gazebo Harmonic). See Phase 3.

Everything else in `Dockerfile` is distro-agnostic: `python3-serial`,
`python3-colcon-common-extensions`, the `ros` user at uid 1000, `entrypoint.sh`.
Keep uid 1000 -- the Pi's `mike` is uid 1000 and the `/workspace` bind mount
and the `/dev/video*` ACL both depend on the match.

### The desktop split may be obsolete

`Dockerfile.desktop` exists solely because `ros-humble-desktop-full` and
`ros-humble-gazebo-ros-pkgs` have no arm64 build. On Jazzy,
**`ros-jazzy-desktop-full` and `ros-jazzy-ros-gz` are both published for
arm64**, so that reasoning no longer holds and the two images could collapse
into one.

Do not collapse them reflexively. The comment in `.devcontainer/pi/devcontainer.json`
gives the other half of the reason: indexing ROS headers and carrying RViz and
Gazebo is the heaviest thing you can ask a Pi to do. Availability is not the
same as advisability. But the split should now be justified on **image size
and Pi load**, not on "it does not exist for arm64", and the comments in both
Dockerfiles and both devcontainer.json files say the latter.

### Naming

`ros2-humble-dev` becomes actively misleading. Renaming touches the GitHub
repo, the GHCR package, `.github/workflows/`, and the `image:` line in both
`.devcontainer/devcontainer.json` and `.devcontainer/pi/devcontainer.json`.

Cheapest correct option: keep the repo name, publish under a new tag
(`0.3.0-jazzy`), and rename later if it stops being a two-distro repo.
Nothing breaks either way -- this is cosmetic, but decide before pushing
tags rather than after.

---

## Phase 2 [CONTAINER] -- the camera

**This phase is now trivial.** It used to be the one that could save the most
work or cost the most time; the hardware change removed it.

The IMX500 CSI camera was returned to the store and replaced with a Logitech
C920 on USB. That retires libcamera completely:

  - `camera_ws`, the source-built libcamera 0.7.2 + camera_ros -- **deleted**
  - `setup-camera-ws.sh` -- **deleted**
  - the whole `ros-jazzy-libcamera`-lacks-`libpisp` investigation -- moot

A C920 is a UVC device: it emits finished YUYV/MJPG/H264 frames, which is
exactly what a plain V4L2 node wants. `camera_ros` could not drive it anyway
-- the libcamera we built carries only the `rpi/pisp` and `rpi/vc4` pipeline
handlers, with no `uvcvideo` handler, and the host's packaged libcamera is
the same (`rpicam-hello --list-cameras` -> `No cameras available!`).

So Phase 2 is one apt line:

    sudo apt-get install -y ros-jazzy-usb-cam

Note this reverses the "v4l2_camera is NOT usable" finding in the old
`README-camera.md`. That was correct **for the CSI path** -- the Pi 5 CSI
front-end emits packed raw Bayer that only libcamera can push through the
PiSP ISP. It says nothing about a UVC webcam.

Camera parameters for the C920 live in `mybot_detection`'s launch file. The
one number worth remembering: YUYV tops out at 30 fps up to 800x448, and
720p YUYV is only 10 fps. Use 640x480 unless you have a reason not to, or
switch to MJPG and pay a JPEG decode per frame.

---

## Phase 3 [WORKSTATION] -- Gazebo Classic to Harmonic

The largest piece, and the only one that cannot be verified on the Pi:
simulation runs on the workstation.

Jazzy pairs with **Gazebo Harmonic** (`gz-sim` 8), via `ros_gz`. Three
Classic plugins are in use across seven files:

| Classic | Harmonic |
|---|---|
| `libgazebo_ros_diff_drive.so` | `gz-sim-diff-drive-system`, name `gz::sim::systems::DiffDrive` |
| `libgazebo_ros_camera.so` | `gz-sim-sensors-system` + `<sensor type="camera">` |
| `libgazebo_ros_ray_sensor.so` | `gz-sim-sensors-system` + `<sensor type="gpu_lidar">` |

Files touched:

    ros2_ws/src/mybot/description/camera.xacro
    ros2_ws/src/mybot/description/example_gazebo.xacro
    ros2_ws/src/mybot/description/gazebo_control.xacro
    ros2_ws/src/mybot/description/lidar.xacro
    ros2_ws/src/mybot/description/robot_core.xacro
    ros2_ws/src/mybot_gazebo/package.xml
    ros2_ws/src/mybot_gazebo/launch/rsp_sim.launch.py

Three structural differences, not just renames:

  - **`<sensor type="ray">` becomes `type="gpu_lidar"`.** The element
    layout changes too, not only the type string.
  - **Topics are no longer published into ROS directly.** Gazebo publishes
    on its own transport; `ros_gz_bridge` maps each one explicitly, either
    with CLI arguments or a YAML config. Every topic the launch files and
    RViz configs assume must get a bridge entry, or it silently does not
    appear. This is the main source of "it launches and nothing happens".
  - **`package.xml` deps** change from `gazebo_ros` / `gazebo_ros_pkgs` to
    `ros_gz_sim` / `ros_gz_bridge`.

`mybot_gazebo` carries a COLCON_IGNORE note about being workstation-only.
Revisit it: `ros-jazzy-ros-gz` **is** published for arm64, so the package is
now installable on the Pi even if running a simulator there remains a bad
idea.

Keep the Classic version reachable (a branch or a tag) until the Harmonic
version is confirmed working -- there is no incremental path back.

---

## Phase 4 -- verification

Reuse the Pi OS runbook's checklist; these are the Jazzy-specific additions.

- [ ] `ros-jazzy-*` image builds for **both** arm64 and amd64 in CI
- [ ] container starts from `.devcontainer/pi/devcontainer.json` with its 7
      devices, `--group-add=video`, `--init`
- [ ] `id` inside the container -> uid 1000, `dialout` and `video`
- [ ] camera publishes `/image_raw` at ~30 Hz from `usb_cam` on `/dev/webcam`
- [ ] `hailortcli fw-control identify` inside the container reports
      HAILO10H, and the HailoRT version matches the host driver exactly
- [ ] lidar publishes `/scan` with `frame_id: laser_frame`
- [ ] motors respond on `/dev/motor` at 57600
- [ ] `[WORKSTATION]` Gazebo Harmonic launches, robot spawns
- [ ] `[WORKSTATION]` bridged topics actually appear in `ros2 topic list` --
      check each one, absence is silent
- [ ] DDS discovery still works Pi <-> workstation across the distro change

---

## Traps carried over

The libcamera traps (the `ldd` target, the `/run/udev` mount, the
`camera_ws/` gitignore) died with the CSI camera. What still applies:

  - the camera needs `--group-add=video`, the ACL is not tracked
  - `/dev/videoN` numbering is not stable across reboots -- use the
    `/dev/webcam` udev symlink, and note the C920 claims two nodes where only
    `ATTR{index}=="0"` captures
  - HailoRT userspace in the image must match the host kernel driver version
    exactly; the host driver is DKMS-built and apt-managed, and must not be
    replaced by Hailo's own driver .deb
