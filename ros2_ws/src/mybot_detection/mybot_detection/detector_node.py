"""Object detection on the Hailo-10H NPU.

Subscribes to a camera image, runs a YOLO HEF on the accelerator, and
publishes vision_msgs/Detection2DArray plus (optionally) the same image with
boxes drawn on it -- the ROS equivalent of what hailo-webcam-detect.sh shows.

Two things about this model shape the code, both verified against
yolov11m_h10.hef with `hailortcli parse-hef` and a live inference:

  1. NMS runs ON-CHIP. The output vstream is
     `yolov8_nms_postprocess FLOAT32, HAILO NMS BY CLASS`, and pyhailort
     hands back a list of 80 (N, 5) arrays -- one per COCO class, already
     deduplicated. There is no decode, no anchor math and no NMS to do here,
     and TAPPAS is not needed at all.
  2. Each detection is (ymin, xmin, ymax, xmax, score), normalised 0..1
     against the 640x640 LETTERBOXED input -- not against the source frame.
     Undoing that padding is the whole job of _to_pixels(). Get it wrong and
     every box is offset; it is the most likely cause of "boxes are in the
     wrong place". Values can land slightly outside 0..1 (xmax=1.0008 was
     observed on a box touching the frame edge), so results are clamped.
"""

import numpy as np
import rclpy
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from vision_msgs.msg import (
    BoundingBox2D,
    Detection2D,
    Detection2DArray,
    ObjectHypothesisWithPose,
)

from mybot_detection.coco_classes import COCO_CLASSES, class_name

# Imported lazily-ish so the failure message is useful. pyhailort has no
# rosdep key: it comes from the container image, and its version must match
# the host's PCIe driver exactly.
try:
    import cv2
    from cv_bridge import CvBridge
    from hailo_platform import (
        FormatType,
        HailoSchedulingAlgorithm,
        VDevice,
    )
except ImportError as exc:  # pragma: no cover - environment problem, not logic
    raise ImportError(
        f'{exc}. This node needs the ros2-hailo-dev image: HailoRT userspace '
        'matching the host driver, plus cv_bridge and OpenCV.'
    ) from exc

# The HEF's input tensor is NHWC(640x640x3).
INPUT_SIZE = 640
# Letterbox fill. 114 is the YOLO convention; the value only matters in that
# it should not look like a real object.
PAD_VALUE = 114


class DetectorNode(Node):
    """Runs a Hailo HEF over an image topic and publishes detections."""

    def __init__(self) -> None:
        super().__init__('detector')

        self.declare_parameter('hef_path', '/usr/share/hailo-models/yolov11m_h10.hef')
        self.declare_parameter('score_threshold', 0.40)
        self.declare_parameter('publish_annotated', True)
        self.declare_parameter('input_topic', '/image_raw')
        self.declare_parameter('frame_id', 'camera_link_optical')
        # Allow-list of COCO names; empty (the default) means publish all 80.
        #   --ros-args -p class_filter:="[person,chair]"
        #
        # dynamic_typing is required, not decoration. rclpy infers a
        # parameter's type from its DEFAULT, and an empty list infers as
        # BYTE_ARRAY, so passing class names to it dies with
        # InvalidParameterTypeException before the node starts. Setting
        # descriptor.type does not help -- the inferred type still wins.
        # Verified on Jazzy; the alternative is a [''] default, which works
        # but makes `ros2 param get` report a list containing one empty name.
        self.declare_parameter(
            'class_filter', [],
            ParameterDescriptor(
                dynamic_typing=True,
                description='COCO class names to publish; empty means all.'))

        self._hef_path = self.get_parameter('hef_path').value
        self._score_threshold = float(self.get_parameter('score_threshold').value)
        self._publish_annotated = bool(self.get_parameter('publish_annotated').value)
        input_topic = self.get_parameter('input_topic').value
        self._frame_id = self.get_parameter('frame_id').value
        self._class_filter = self._read_class_filter()

        self._bridge = CvBridge()
        self._busy = False
        self._dropped = 0

        self._start_hailo()

        self._det_pub = self.create_publisher(Detection2DArray, '/detections', 10)
        self._img_pub = (
            self.create_publisher(Image, '/detections/image_annotated', 1)
            if self._publish_annotated else None
        )

        # Sensor QoS is best-effort: with a depth of 1, a frame that arrives
        # while inference is running replaces the queued one instead of
        # queueing behind it. Same intent as `leaky=downstream` in the
        # GStreamer script -- prefer a current frame over a backlog.
        self.create_subscription(
            Image, input_topic, self._on_image, qos_profile_sensor_data)

        self.get_logger().info(
            f'detector ready: hef={self._hef_path} topic={input_topic} '
            f'threshold={self._score_threshold} annotated={self._publish_annotated} '
            f'classes={sorted(self._class_filter) if self._class_filter else "all"}')

    def _read_class_filter(self) -> set:
        """Validated allow-list of COCO names; an empty set means no filtering.

        A typo would otherwise filter out everything and look exactly like a
        broken detector, so unknown names are reported and dropped.
        """
        requested = [str(n).strip() for n in self.get_parameter('class_filter').value]
        requested = [n for n in requested if n]
        if not requested:
            return set()

        known = {n for n in requested if n in COCO_CLASSES}
        unknown = [n for n in requested if n not in COCO_CLASSES]
        if unknown:
            self.get_logger().warn(
                f'class_filter: ignoring unknown COCO class(es) {unknown}')
        if not known:
            self.get_logger().warn(
                'class_filter: no valid classes left, publishing ALL classes')
        return known

    # -- Hailo ------------------------------------------------------------

    def _start_hailo(self) -> None:
        """Configure the NPU once, at startup.

        Reconfiguring per frame costs far more than inference itself, so the
        VDevice and the configured model are held for the node's lifetime.
        """
        params = VDevice.create_params()
        params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
        self._vdevice = VDevice(params)

        self._model = self._vdevice.create_infer_model(self._hef_path)
        self._model.set_batch_size(1)
        self._model.input().set_format_type(FormatType.UINT8)
        self._model.output().set_format_type(FormatType.FLOAT32)

        # configure() returns a context manager; keep it open for the node's
        # life rather than entering it per frame. Hold the manager itself, not
        # just the value, so teardown closes THIS context and not a new one.
        self._config_ctx = self._model.configure()
        self._configured = self._config_ctx.__enter__()
        self._out_name = self._model.output().name
        self._out_shape = self._model.output().shape

        self.get_logger().info(
            f'hailo configured: output {self._out_name} shape {self._out_shape}')

    def _infer(self, letterboxed: np.ndarray) -> list:
        """Run one frame. Returns the per-class list of (N, 5) arrays."""
        # An NMS output has to be bound to a real buffer. create_bindings()
        # with no output_buffers requests a view, which this output does not
        # support ("not configured as view").
        buf = np.empty(self._out_shape, dtype=np.float32)
        bindings = self._configured.create_bindings(output_buffers={self._out_name: buf})
        bindings.input().set_buffer(letterboxed)
        self._configured.run([bindings], 10000)
        return bindings.output().get_buffer()

    # -- geometry ---------------------------------------------------------

    @staticmethod
    def _letterbox(rgb: np.ndarray):
        """Scale to fit INPUT_SIZE preserving aspect, pad to square.

        Returns (canvas, scale, pad_x, pad_y). Stretching instead of padding
        would distort every object and cost accuracy, which is why
        videoscale add-borders=true is set in the GStreamer script too.
        """
        h, w = rgb.shape[:2]
        scale = INPUT_SIZE / max(h, w)
        new_h, new_w = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), PAD_VALUE, dtype=np.uint8)
        pad_x = (INPUT_SIZE - new_w) // 2
        pad_y = (INPUT_SIZE - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        return canvas, scale, pad_x, pad_y

    @staticmethod
    def _to_pixels(det, scale, pad_x, pad_y, width, height):
        """Map one detection back to source-image pixels.

        `det` is (ymin, xmin, ymax, xmax, score), normalised against the
        padded 640x640 input. Undo the padding, then the scale, then clamp --
        the model can return coordinates a hair outside 0..1.
        """
        ymin, xmin, ymax, xmax = (float(v) * INPUT_SIZE for v in det[:4])

        x0 = (xmin - pad_x) / scale
        x1 = (xmax - pad_x) / scale
        y0 = (ymin - pad_y) / scale
        y1 = (ymax - pad_y) / scale

        x0 = min(max(x0, 0.0), width)
        x1 = min(max(x1, 0.0), width)
        y0 = min(max(y0, 0.0), height)
        y1 = min(max(y1, 0.0), height)
        return x0, y0, x1, y1

    # -- callback ---------------------------------------------------------

    def _on_image(self, msg: Image) -> None:
        if self._busy:
            # Single-threaded executor makes this defensive rather than
            # essential, but it keeps the drop explicit and countable.
            self._dropped += 1
            return
        self._busy = True
        try:
            self._process(msg)
        except Exception:  # noqa: BLE001 - one bad frame must not kill the node
            self.get_logger().exception('frame dropped: inference failed')
        finally:
            self._busy = False

    def _process(self, msg: Image) -> None:
        rgb = self._bridge.imgmsg_to_cv2(msg, desired_encoding='rgb8')
        height, width = rgb.shape[:2]

        letterboxed, scale, pad_x, pad_y = self._letterbox(rgb)
        per_class = self._infer(letterboxed)

        array = Detection2DArray()
        array.header = msg.header
        # usb_cam is told to stamp camera_link_optical, but a bare v4l2 node
        # or a bag may not be, so fall back to the parameter.
        if not array.header.frame_id:
            array.header.frame_id = self._frame_id

        annotated = rgb.copy() if self._img_pub is not None else None

        for class_id, detections in enumerate(per_class):
            detections = np.asarray(detections)
            if detections.size == 0:
                continue
            # Filter by class before looking at any box: the whole class is
            # in or out, so this is one lookup instead of one per detection.
            name = class_name(class_id)
            if self._class_filter and name not in self._class_filter:
                continue
            for det in detections:
                score = float(det[4])
                if score < self._score_threshold:
                    continue
                x0, y0, x1, y1 = self._to_pixels(
                    det, scale, pad_x, pad_y, width, height)
                if x1 <= x0 or y1 <= y0:
                    continue

                array.detections.append(
                    self._make_detection(array.header, name, score, x0, y0, x1, y1))

                if annotated is not None:
                    self._draw(annotated, name, score, x0, y0, x1, y1)

        self._det_pub.publish(array)

        if annotated is not None:
            out = self._bridge.cv2_to_imgmsg(annotated, encoding='rgb8')
            out.header = array.header
            self._img_pub.publish(out)

    @staticmethod
    def _make_detection(header, name, score, x0, y0, x1, y1) -> Detection2D:
        detection = Detection2D()
        detection.header = header
        detection.id = name

        bbox = BoundingBox2D()
        # vision_msgs 4.x BoundingBox2D.center is a vision_msgs/Pose2D, which
        # nests position in a Point2D. Pre-4.x used geometry_msgs/Pose2D with
        # x/y directly, so older examples will not compile here.
        bbox.center.position.x = (x0 + x1) / 2.0
        bbox.center.position.y = (y0 + y1) / 2.0
        bbox.center.theta = 0.0
        bbox.size_x = x1 - x0
        bbox.size_y = y1 - y0
        detection.bbox = bbox

        hypothesis = ObjectHypothesisWithPose()
        # Also 4.x: class_id is a STRING, and lives under .hypothesis.
        hypothesis.hypothesis.class_id = name
        hypothesis.hypothesis.score = score
        detection.results.append(hypothesis)
        return detection

    @staticmethod
    def _draw(image, name, score, x0, y0, x1, y1) -> None:
        p0 = (int(x0), int(y0))
        p1 = (int(x1), int(y1))
        cv2.rectangle(image, p0, p1, (0, 255, 0), 2)
        label = f'{name} {score:.2f}'
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        # Keep the label inside the frame when the box hugs the top edge.
        ty = max(int(y0), th + 4)
        cv2.rectangle(image, (p0[0], ty - th - 4), (p0[0] + tw + 4, ty), (0, 255, 0), -1)
        cv2.putText(image, label, (p0[0] + 2, ty - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    def destroy_node(self) -> bool:
        if self._dropped:
            self.get_logger().info(f'dropped {self._dropped} frames while busy')
        try:
            self._config_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = DetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
