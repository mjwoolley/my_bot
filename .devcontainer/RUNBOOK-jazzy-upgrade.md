# Runbook: ROS 2 Humble -> Jazzy

Goal: move the container image, this workspace and the simulation from
Humble (Ubuntu 22.04) to Jazzy (Ubuntu 24.04), without losing the Pi 5
camera that took the Pi OS migration to get working.

Status: **done, 2026-09-02.** Written 2026-08-28; Phases 1, 2 and 4 (the
robot) landed first, Phase 3 (the simulation) followed. Package availability
below was checked against the live ROS 2 apt index for `noble` on 2026-08-28
and re-confirmed on completion.

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
| `ros2-humble-dev` -> **`ros2-sim-dev`** | base image, package names, rosdep distro, and the two variants collapsed into one. Renamed; see naming below. |
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

In `~/robotics/docker/ros2-sim-dev` (was `ros2-humble-dev`):

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

**DONE.** `ros2-humble-dev` became actively misleading the moment nothing used
Humble, which is the condition this section set for renaming. The repo, the
GHCR package and the `image:` line in `.devcontainer/devcontainer.json` all say
`ros2-sim-dev` now, and the name says what the image is FOR rather than which
distro it happened to carry -- so the next distro bump does not make it wrong
again.

GitHub redirects the old repo URL, so existing clones keep working. The GHCR
package does NOT follow a rename, and that turned out to be the useful half:
`ghcr.io/mjwoolley/ros2-humble-dev:0.2.0-desktop` stays pullable forever as the
frozen Gazebo Classic fallback, while Jazzy publishes under the new name. Clean
cut, nothing rewritten.

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

**DONE.** The mapping predicted here was right; what it did not predict was
where the time actually went. Recorded below as the element-level differences,
because "swap the plugin name" is not what this was.

| Classic | Harmonic | Note |
|---|---|---|
| `libgazebo_ros_diff_drive.so` | `gz-sim-diff-drive-system` / `gz::sim::systems::DiffDrive` | |
| `<wheel_diameter>0.1</>` | `<wheel_radius>0.05</>` | **The trap.** gz ignores the unknown element and falls back to its DEFAULT radius of 0.2 m: four times too fast, four times wrong odometry, no warning. |
| `<max_wheel_torque>200</>` | *gone* | gz-sim 8 DiffDrive drives joints with a velocity command; there is no torque limit. `<max_wheel_acceleration>` becomes body-level `<max_linear_acceleration>`. |
| `<publish_wheel_tf>true</>` | `gz::sim::systems::JointStatePublisher` + a `/joint_states` bridge entry | No direct equivalent. Miss it and the robot drives with frozen wheels in RViz -- TF stays complete, so nothing looks broken. |
| `libgazebo_ros_ray_sensor.so` | `gz-sim-sensors-system` + `<sensor type="gpu_lidar">` | The container element is `<lidar>`, not `<ray>`. |
| `libgazebo_ros_camera.so` | `gz-sim-sensors-system` + `<sensor type="camera">` | `camera_info` is derived by replacing the last element of `<topic>`. |
| plugin `<frame_name>` | `<gz_frame_id>` | Required, not cosmetic: sdformat lumps fixed joints, so the default `frame_id` is a mangled `laser_frame_fixed_joint_lump__laser`. `gz sdf -p` warns that `gz_frame_id` is "not defined in SDF" -- that warning is expected, gz-sensors reads it anyway. |
| `<material>Gazebo/Blue</material>` | *delete it* | Do NOT convert. sdformat turns it into a `<script>` that REPLACES the `<ambient>`/`<diffuse>` already generated from the URDF `<material>`. ogre2 has no Ogre material scripts, so converting gives you white wheels -- worse than deleting. |
| Classic `.world` | SDF 1.10 `.sdf` | And every world must now declare Physics, UserCommands, SceneBroadcaster and Sensors explicitly: gz-sim loads its defaults only if the file declares no `<plugin>` at all, and each omission fails silently. |
| `model://construction_barrel` | Fuel URI | Classic's model database does not exist in Harmonic. |

Files changed:

    ros2_ws/src/mybot/description/camera.xacro
    ros2_ws/src/mybot/description/gazebo_control.xacro
    ros2_ws/src/mybot/description/lidar.xacro
    ros2_ws/src/mybot/description/robot_core.xacro
    ros2_ws/src/mybot/description/robot.urdf.xacro
    ros2_ws/src/mybot_gazebo/package.xml
    ros2_ws/src/mybot_gazebo/launch/rsp_sim.launch.py
    ros2_ws/src/mybot_gazebo/config/gz_bridge.yaml     (new)
    ros2_ws/src/mybot_gazebo/config/drive_bot.rviz
    ros2_ws/src/mybot_gazebo/worlds/empty.sdf          (was empty.world)
    ros2_ws/src/mybot_gazebo/worlds/obstacles.sdf      (was obstacles.world)

`example_gazebo.xacro` was on this list when it was written; it was deleted as
dead code in `0c02545` before the port started.

`mybot_gazebo`'s tracked `COLCON_IGNORE` is gone and the package builds
everywhere. The Pi cannot RESOLVE its deps -- `ros_gz_sim` and `ros_gz_bridge`
are deliberately absent from `ros2-hailo-dev` -- so build there with
`--skip-keys "ros_gz_sim ros_gz_bridge"`, or drop an untracked `COLCON_IGNORE`.

The Classic version is reachable at the tag **`gazebo-classic-final`**, and the
last Classic image stays pullable at
`ghcr.io/mjwoolley/ros2-humble-dev:0.2.0-desktop` -- a GHCR package does not
follow a repository rename, which is useful here rather than a nuisance.

### Two things that cost time and are not in the table

**`gz sim` starts PAUSED without `-r`.** No error, no output. `/clock` never
ticks, so every node with `use_sim_time` blocks forever and RViz sits empty.
`rsp_sim.launch.py` puts `-r` in its `gz_args` default; a hand-typed
`gz sim world.sdf` does not.

**A missing bridge entry is silent.** Under Classic the plugins published into
ROS directly. Harmonic publishes on gz-transport and `ros_gz_bridge` maps each
topic explicitly; an omission means the topic simply is not there. Check the gz
side first with `gz topic -l` -- that separates "Gazebo is not producing it"
from "the bridge is not carrying it".

## Phase 4 -- verification

Reuse the Pi OS runbook's checklist; these are the Jazzy-specific additions.

- [x] `ros-jazzy-*` image builds in CI (amd64 only now -- the Pi has its own
      image, so the arm64 half of this repo was retired rather than ported)
- [x] container starts from `.devcontainer/pi/devcontainer.json` with its 7
      devices, `--group-add=video`, `--init`
- [x] `id` inside the container -> uid 1000, `dialout` and `video`
- [x] camera publishes `/image_raw` at ~30 Hz from `usb_cam` on `/dev/webcam`
- [x] `hailortcli fw-control identify` inside the container reports
      HAILO10H, and the HailoRT version matches the host driver exactly
- [x] lidar publishes `/scan` with `frame_id: laser_frame`
- [x] motors respond on `/dev/motor` at 57600
- [x] `[WORKSTATION]` Gazebo Harmonic 8.11.0 launches, robot spawns
      ("Entity creation successful")
- [x] `[WORKSTATION]` all 8 bridged topics appear in `ros2 topic list`:
      `/clock` 1000 Hz, `/odom` 29.4 Hz, `/tf` 50 Hz, `/joint_states`,
      `/scan` 9.9 Hz, `/camera/image_raw` 9.8 Hz, `/camera/camera_info` 9.9 Hz,
      `/cmd_vel`
- [x] `[WORKSTATION]` frame_ids correct: `/scan` -> `laser_frame`, camera ->
      `camera_link_optical`, `/odom` `odom` -> `base_link`
- [x] `[WORKSTATION]` TF is one connected tree rooted at `odom`
- [x] `[WORKSTATION]` commanded 0.2 m/s reads back as 0.2 on `/odom`
      (the `wheel_radius` check -- 0.8 would mean the default leaked through)
- [x] `[WORKSTATION]` `obstacles.sdf` loads all 9 Fuel models; lidar returns
      94 of 500 beams, nearest hit 1.68 m
- [x] DDS discovery still works Pi <-> workstation across the distro change

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

New ones, from the Harmonic port:

  - `ros2 topic hz` reports nothing for the sensor topics even when they are
    publishing fine. Do not trust it here; count messages with a small rclpy
    subscriber on `qos_profile_sensor_data`, or use `ros2 topic echo --once`.
  - `<update_rate>` on `JointStatePublisher` is IGNORED by gz-sim 8.11.0 -- the
    feature postdates the release Jazzy ships. `/joint_states` therefore runs at
    the 1000 Hz physics step. The element is left in place for the next bump.
  - Headless (`-s`) runs fall back to EGL, where glvnd hands the NVIDIA device
    to Mesa's dri2 loader and it fails. Sensors still render in software, so it
    looks fine and is merely slow. `__EGL_VENDOR_LIBRARY_FILENAMES` is pinned in
    the workstation devcontainer to avoid it.
  - `gz sdf -k` cannot validate a world containing Fuel `<include>` URIs -- it
    has no fetch callback and reports "Unable to find uri". `gz sim` resolves
    them fine. Validate those worlds by running them, not by checking them.
