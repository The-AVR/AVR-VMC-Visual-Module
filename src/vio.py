import math
from typing import Tuple

import config
import numpy as np
from bell.avr.mqtt.module import MQTTModule
from bell.avr.mqtt.payloads import (
    AVRVIOAttitudeEulerRadians,
    AVRVIOConfidence,
    AVRVIOHeading,
    AVRVIOPositionLocal,
    AVRVIOResync,
    AVRVIOVelocity,
)
from bell.avr.utils.decorators import run_forever, try_except
from loguru import logger
from vio_library import CameraCoordinateTransformation
from zed_library import ZEDCamera


class VIOModule(MQTTModule):
    def __init__(self):
        super().__init__()

        # record if sync has happend once
        self.init_sync = False

        # connected libraries
        self.camera = ZEDCamera()
        self.coord_trans = CameraCoordinateTransformation()

        # mqtt
        self.topic_callbacks = {"avr/vio/resync": self.handle_resync}

    def handle_resync(self, payload: AVRVIOResync) -> None:
        # whenever new data is published to the ZEDCamera resync topic, we need to compute a new correction
        # to compensate for sensor drift over time.
        if not self.init_sync or config.CONTINUOUS_SYNC:
            self.coord_trans.sync(payload)
            self.init_sync = True

    @try_except(reraise=False)
    def publish_updates(
        self,
        ned_pos: Tuple[float, float, float],
        ned_vel: Tuple[float, float, float],
        rpy: Tuple[float, float, float],
        tracker_confidence: float,
    ) -> None:
        if np.isnan(ned_pos).any():
            raise ValueError("Camera has NaNs for position")

        # send position update
        self.send_message(
            "avr/vio/position/local",
            AVRVIOPositionLocal(n=ned_pos[0], e=ned_pos[1], d=ned_pos[2]),
        )

        if np.isnan(rpy).any():
            raise ValueError("Camera has NaNs for orientation")

        # send orientation update
        self.send_message(
            "avr/vio/attitude/euler/radians",
            AVRVIOAttitudeEulerRadians(psi=rpy[0], theta=rpy[1], phi=rpy[2]),
        )

        # send heading update
        heading = rpy[2]
        # correct for negative heading
        if heading < 0:
            heading += 2 * math.pi
        heading = np.rad2deg(heading)
        self.send_message("avr/vio/heading", AVRVIOHeading(hdg=heading))
        # coord_trans.heading = rpy[2]

        if np.isnan(ned_vel).any():
            raise ValueError("Camera has NaNs for velocity")

        # send velocity update
        self.send_message(
            "avr/vio/velocity",
            AVRVIOVelocity(Vn=ned_vel[0], Ve=ned_vel[1], Vd=ned_vel[2]),
        )

        self.send_message(
            "avr/vio/confidence",
            AVRVIOConfidence(
                tracking=tracker_confidence,
            ),
        )

    @run_forever(frequency=config.CAM_UPDATE_FREQ)
    @try_except(reraise=False)
    def process_camera_data(self) -> None:
        data = self.camera.get_pipe_data()

        if data is None:
            logger.debug("Waiting on camera data")
            return

        # collect data from the sensor and transform it into "global" NED frame
        (
            ned_pos,
            ned_vel,
            rpy,
        ) = self.coord_trans.transform_trackcamera_to_global_ned(data)

        self.publish_updates(
            tuple(ned_pos),
            tuple(ned_vel),
            rpy,
            data["tracker_confidence"],
        )

    def run(self) -> None:
        self.run_non_blocking()

        # setup the tracking camera
        logger.debug("Setting up camera connection")
        self.camera.setup()

        # begin processing data
        self.process_camera_data()


if __name__ == "__main__":
    vio = VIOModule()
    vio.run()
