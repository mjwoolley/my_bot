import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'mybot_detection'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # ament_python does not install launch files for you, and the vendored
        # serial_motor_demo (this workspace's only other ament_python package)
        # has no launch dir to copy the idiom from.
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='mwoolley2',
    maintainer_email='mwoolley2@gmail.com',
    description='Object detection on the Hailo-10H NPU.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'detector = mybot_detection.detector_node:main',
        ],
    },
)
