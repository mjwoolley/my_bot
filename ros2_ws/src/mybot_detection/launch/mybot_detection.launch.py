from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():

    publish_annotated = LaunchConfiguration('publish_annotated')
    score_threshold = LaunchConfiguration('score_threshold')
    class_filter = LaunchConfiguration('class_filter')

    # Launch!
    return LaunchDescription([

        DeclareLaunchArgument(
            'publish_annotated',
            default_value='true',
            description='Publish /detections/image_annotated with boxes drawn '
                        'on the frame. Set false on the robot to save the '
                        'draw and the second image topic.'
        ),

        DeclareLaunchArgument(
            'score_threshold',
            default_value='0.40',
            description='Drop detections below this score. The HEF already '
                        'applies 0.200 on-chip, so this can only be stricter.'
        ),

        DeclareLaunchArgument(
            'class_filter',
            default_value='[]',
            description='Allow-list of COCO class names, e.g. "[person,chair]". '
                        'Empty publishes all 80. Filtering happens after '
                        'inference -- the NPU still evaluates every class, so '
                        'this cuts topic traffic and clutter, not NPU load.'
        ),

        Node(
            package='usb_cam',
            executable='usb_cam_node_exe',
            output='screen',
            parameters=[{
                # Set by run-container.sh, which resolves the /dev/webcam udev
                # symlink to the real node. It must be the REAL /dev/videoN
                # path: usb_cam only accepts devices it can find by walking
                # /sys/class/video4linux, so a node passed under an alias is
                # rejected as "not a valid V4L2 device" even when it opens
                # fine. See .devcontainer/pi/udev/99-webcam-c920.rules -- the
                # C920 claims two nodes and only index 0 captures.
                'video_device': EnvironmentVariable(
                    'MYBOT_VIDEO_DEVICE', default_value='/dev/video0'),
                # YUYV is uncompressed, so no JPEG decode per frame. The model
                # input is 640x640 anyway, so 720p would buy nothing -- and
                # the C920 only manages 10 fps at 720p in YUYV vs 30 at 480p.
                'pixel_format': 'yuyv2rgb',
                'image_width': 640,
                'image_height': 480,
                'framerate': 30.0,
                # Matches camera_link_optical in mybot/description/camera.xacro,
                # which is the frame detections are reported in -- the detector
                # copies the image header, so this is what stamps /detections.
                # NOTE the key is `frame_id`, not `camera_frame_id`: usb_cam
                # 0.8.1 ignores unknown parameters silently, and the frame
                # quietly defaults to the camera_name ("default_cam").
                'frame_id': 'camera_link_optical',
            }]
        ),

        Node(
            package='mybot_detection',
            executable='detector',
            output='screen',
            parameters=[{
                # 80-class COCO YOLOv11m, compiled for the Hailo-10H. NMS runs
                # on-chip, so nothing here decodes raw tensors.
                'hef_path': '/usr/share/hailo-models/yolov11m_h10.hef',
                'score_threshold': score_threshold,
                'publish_annotated': publish_annotated,
                'input_topic': '/image_raw',
                'frame_id': 'camera_link_optical',
                'class_filter': ParameterValue(class_filter, value_type=None),
            }]
        ),
    ])
