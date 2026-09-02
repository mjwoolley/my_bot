import os

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (AppendEnvironmentVariable, DeclareLaunchArgument,
                            IncludeLaunchDescription, TimerAction)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

import xacro


def generate_launch_description():

    pkg_mybot = get_package_share_directory('mybot')
    pkg_mybot_gazebo = get_package_share_directory('mybot_gazebo')
    pkg_ros_gz_sim = get_package_share_directory('ros_gz_sim')

    # use_sim pulls in gazebo_control.xacro and the two <sensor> blocks, which
    # the description omits by default so the real robot never carries them.
    xacro_file = os.path.join(pkg_mybot, 'description', 'robot.urdf.xacro')
    robot_description_raw = xacro.process_file(
        xacro_file, mappings={'use_sim': 'true'}).toxml()

    # Name of a .sdf file in mybot_gazebo/worlds (e.g. obstacles.sdf)
    world = PathJoinSubstitution(
        [FindPackageShare('mybot_gazebo'), 'worlds', LaunchConfiguration('world')])

    # MUST be ordered before the gz_sim include below. gz_sim.launch.py reads
    # GZ_SIM_RESOURCE_PATH out of os.environ inside an OpaqueFunction at
    # execution time, so an env action placed after it silently has no effect --
    # and the symptom surfaces three layers away as a mesh that will not resolve.
    #
    # Append, never SetEnvironmentVariable: the ros_gz vendor packages already
    # populate this, and clobbering it breaks Fuel and the built-in worlds.
    resource_path = AppendEnvironmentVariable(
        'GZ_SIM_RESOURCE_PATH', os.path.join(pkg_mybot_gazebo, 'worlds'))

    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_ros_gz_sim, 'launch', 'gz_sim.launch.py')),
        launch_arguments={
            'gz_args': [LaunchConfiguration('gz_args'), ' ', world],
            'on_exit_shutdown': 'true',
        }.items(),
    )

    node_robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description_raw,
                     'use_sim_time': True}]
    )

    # Nothing crosses from Gazebo into ROS without this. See config/gz_bridge.yaml
    # -- an unbridged topic is silently absent, not an error.
    bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        name='gz_bridge',
        output='screen',
        parameters=[{
            'config_file': os.path.join(pkg_mybot_gazebo, 'config', 'gz_bridge.yaml'),
            'use_sim_time': True,
        }],
    )

    # Delayed rather than raced. `create` waits only 5000 ms for the world's
    # /create service -- the timeout is hard-coded in ros_gz_sim/src/create.cpp
    # -- and on a cold start ogre2 plus the GUI can take longer than that, at
    # which point the node exits and no robot appears.
    spawn_entity = TimerAction(period=5.0, actions=[
        Node(package='ros_gz_sim', executable='create',
             output='screen',
             arguments=['-topic', 'robot_description',
                        '-name', 'my_bot',
                        # base_link sits at the wheel axle, r=0.05. Spawning at
                        # z=0 buries the wheels in the ground plane and the
                        # solver flings the robot into the air on the first step.
                        '-z', '0.1']),
    ])

    # use_sim_time matters here as much as on the publishers: an RViz running on
    # wall-clock against sim-time TF reports "no transform from [odom] to
    # [base_link]" and extrapolation errors, which reads exactly like a broken
    # TF tree rather than a clock mismatch.
    rviz = Node(
        package='rviz2', executable='rviz2', output='screen',
        condition=IfCondition(LaunchConfiguration('rviz')),
        arguments=['-d', os.path.join(pkg_mybot_gazebo, 'config', 'drive_bot.rviz')],
        parameters=[{'use_sim_time': True}],
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'world',
            default_value='empty.sdf',
            description='World file in mybot_gazebo/worlds, e.g. obstacles.sdf'),

        DeclareLaunchArgument(
            'gz_args',
            default_value='-r -v 4',
            description='Flags for gz sim. -r RUNS the simulation -- without it '
                        'gz starts paused, /clock never ticks, and every node '
                        'with use_sim_time blocks forever with no error. '
                        'Add -s for headless; drop -v 4 for a quiet console.'),

        DeclareLaunchArgument(
            'rviz',
            default_value='false',
            description='Also open RViz with config/drive_bot.rviz'),

        resource_path,   # must precede gz_sim
        gz_sim,
        node_robot_state_publisher,
        bridge,
        spawn_entity,
        rviz,
    ])
