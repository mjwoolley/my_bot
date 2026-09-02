from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():

    # Launch!
    return LaunchDescription([

        Node(
            package='serial_motor_demo',
            executable='driver',
            output='screen',
            parameters=[{
                # Measured on the robot: reset the counters with `r`, turn a
                # wheel one revolution, read `e`. NOT the 3440 in the vendored
                # serial_motor_demo README -- that is upstream's robot.
                'encoder_cpr': 690,
                # Must match PID_RATE in the ros_arduino_bridge sketch; the
                # `m` command speaks in ticks per PID loop.
                'loop_rate': 30,
                'serial_port': '/dev/motor',
                'baud_rate': 57600,
            }]
        )
    ])
