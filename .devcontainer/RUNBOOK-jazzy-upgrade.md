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
| `my_bot` (this one) | `mybot_gazebo` and every `<gazebo>` block in the xacro files; both `devcontainer.json` files; `camera_ws` rebuild |

**Do these as separate commits, and ideally separate sittings.** The base
image bump and the Gazebo migration fail independently, and debugging them
together is miserable.

---

## Prerequisite -- close the open item first

The lidar and motors have **not been driven since the Pi OS switch**. They
enumerate and their udev symlinks resolve, but no node has talked to them.
Test them on the current Humble container before changing distro, or a
failure afterwards is ambiguous between the OS switch and the distro jump.

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

**This is the phase that can save the most work or cost the most time.**

Today `camera_ws` builds libcamera 0.7.2 (Raspberry Pi fork, with libpisp
1.5.0) and camera_ros from source, taking 5-15 minutes and needing the apt
block in `setup-camera-ws.sh`. On Jazzy there are packages:

    ros-jazzy-camera-ros    0.6.0    arm64 + amd64
    ros-jazzy-libcamera     0.7.1    arm64 + amd64

0.7.1 versus the 0.7.2 we built, so the version gap is trivial.

**But do not assume this replaces the source build.** `ros-jazzy-libcamera`
lists no `libpisp` dependency:

    Depends: libc6, libgcc-s1, libssl3t64, libstdc++6, libudev1, libyaml-0-2,
             libatomic1, libssl-dev, libudev-dev, libyaml-dev, libyuv-dev,
             python3-dev, ros-jazzy-ros-workspace

The Pi 5 pipeline handler (`rpi/pisp`) needs libpisp. Its absence strongly
suggests the packaged build has no PiSP support and will not drive the
IMX500 -- the same failure mode as Ubuntu's June-2020 libcamera, just less
obvious because the version number looks current.

Settle it in five minutes before planning around it:

    # [CONTAINER, on a Jazzy image]
    sudo apt-get install -y ros-jazzy-camera-ros
    ldd /opt/ros/jazzy/lib/libcamera_component.so | grep -iE 'libcamera|libpisp'
    ros2 run camera_ros camera_node

  - Publishes frames -> delete `setup-camera-ws.sh` and `camera_ws`
    entirely, and Phase 2 is a one-line apt install. Large simplification.
  - `no cameras available`, or no libpisp in the `ldd` output -> keep the
    source build. Change `setup-camera-ws.sh`'s `--skip-keys=libcamera` to
    also skip `ros-jazzy-libcamera`, or rosdep will pull the packaged one
    back in underneath you.

Either way `camera_ws` must be **rebuilt from scratch** -- it is compiled
against Humble on 22.04. Delete it rather than trying an incremental build:

    rm -rf /workspace/camera_ws

Re-verify with the same check that Phase 6 of the Pi OS runbook uses, and
remember it must target `libcamera_component.so`, not `camera_node`.

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
- [ ] container starts from `.devcontainer/pi/devcontainer.json` with all
      37 devices, `/run/udev`, `--group-add=video`, `--init`
- [ ] `id` inside the container -> uid 1000, `dialout` and `video`
- [ ] camera publishes `/camera/image_raw` at ~30 Hz, and `ldd` on
      `libcamera_component.so` shows libcamera **and libpisp** from wherever
      you settled Phase 2
- [ ] lidar publishes `/scan` with `frame_id: laser_frame`
- [ ] motors respond on `/dev/motor` at 57600
- [ ] `[WORKSTATION]` Gazebo Harmonic launches, robot spawns
- [ ] `[WORKSTATION]` bridged topics actually appear in `ros2 topic list` --
      check each one, absence is silent
- [ ] DDS discovery still works Pi <-> workstation across the distro change

---

## Traps carried over

From RUNBOOK-pi-os-switch.md, all still apply after the upgrade:

  - `ldd` must target `libcamera_component.so`; on `camera_node` it prints
    nothing and looks clean either way
  - `/run/udev` must be mounted or libcamera finds no cameras despite every
    node being present
  - the camera needs `--group-add=video`, the ACL is not tracked
  - `camera_ws/` is gitignored; do not let a rebuild add it to the repo
