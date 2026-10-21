#!/usr/bin/env python3
"""Pure CPU proof of the vendor NOMINAL K1/D455 transform, not robot calibration.

Source: Booster 1.7.1.1 K1_22dof.urdf, head_realsense_rgb_joint;
head yaw/pitch and hip origins from the same vendor kinematic description.
Only the standard library is used. No backup/config/device files are read,
and no robot, camera, motion, network, or installation interfaces are opened.
"""
import math
import unittest


def multiply(left, right):
    return [[sum(a * b for a, b in zip(row, column)) for column in zip(*right)] for row in left]


def rotation_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def rotation_y(angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def rotation_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def homogeneous(rotation):
    return [row + [0.0] for row in rotation] + [[0.0, 0.0, 0.0, 1.0]]


def translate(x, y, z):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z], [0.0, 0.0, 0.0, 1.0]]


def chain(*matrices):
    result = matrices[0]
    for matrix in matrices[1:]:
        result = multiply(result, matrix)
    return result


# p_optical = OPTICAL_FROM_BH * p_BH.
# Optical: x right / y down / z front; B-Human camera: x front / y left / z up.
OPTICAL_FROM_BH = [[0.0, -1.0, 0.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]]
VENDOR_OPTICAL_ROLL = -1.835388
VENDOR_OPTICAL_YAW = -1.570796  # vendor file truncation of -pi/2
NOMINAL_BH_PITCH = -math.pi / 2.0 - VENDOR_OPTICAL_ROLL
NOMINAL_CAMERA_MM = (53.617, -11.5, 102.31)


class NominalD455MountTests(unittest.TestCase):
    def assert_matrix_close(self, actual, expected, tolerance=1e-10):
        self.assertEqual((len(actual), len(actual[0])), (len(expected), len(expected[0])))
        for row, (actual_row, expected_row) in enumerate(zip(actual, expected)):
            for column, (a, b) in enumerate(zip(actual_row, expected_row)):
                self.assertTrue(math.isfinite(a) and math.isfinite(b))
                self.assertAlmostEqual(a, b, delta=tolerance, msg=f"matrix[{row}][{column}]")

    def test_optical_basis_conversion(self):
        # Columns describe each B-Human basis vector in optical coordinates.
        self.assertEqual(multiply(OPTICAL_FROM_BH, [[1], [0], [0]]), [[0], [0], [1]])
        self.assertEqual(multiply(OPTICAL_FROM_BH, [[0], [1], [0]]), [[-1], [0], [0]])
        self.assertEqual(multiply(OPTICAL_FROM_BH, [[0], [0], [1]]), [[0], [-1], [0]])

    def test_exact_nominal_rotation(self):
        # URDF uses Rz(yaw) Ry(pitch) Rx(roll); camera optical pitch is zero.
        vendor = chain(rotation_z(-math.pi / 2), rotation_x(VENDOR_OPTICAL_ROLL))
        converted = multiply(vendor, OPTICAL_FROM_BH)
        self.assert_matrix_close(converted, rotation_y(NOMINAL_BH_PITCH))
        self.assertAlmostEqual(math.degrees(NOMINAL_BH_PITCH), 15.15998616895714, places=11)

    def test_vendor_truncated_yaw_negligible_but_not_silently_identical(self):
        vendor = chain(rotation_z(VENDOR_OPTICAL_YAW), rotation_x(VENDOR_OPTICAL_ROLL))
        converted = multiply(vendor, OPTICAL_FROM_BH)
        error = max(abs(a - b) for row_a, row_b in zip(converted, rotation_y(NOMINAL_BH_PITCH)) for a, b in zip(row_a, row_b))
        self.assertGreater(error, 0.0)
        self.assertLess(error, 4e-7)

    def test_positive_pitch_looks_down(self):
        forward = multiply(rotation_y(NOMINAL_BH_PITCH), [[1.0], [0.0], [0.0]])
        self.assertGreater(forward[0][0], 0.0)
        self.assertLess(forward[2][0], 0.0)
        self.assertAlmostEqual(forward[2][0], -math.sin(NOMINAL_BH_PITCH))
        wrong_sign = multiply(rotation_y(-NOMINAL_BH_PITCH), [[1.0], [0.0], [0.0]])
        self.assertGreater(wrong_sign[2][0], 0.0)

    def test_hip_yaw_pitch_height(self):
        # trunk->hipPitch = -77 mm; trunk->headYaw = +214.9 mm;
        # headYaw->headPitch = +33 mm parallel to the yaw axis.
        self.assertAlmostEqual(77.0 + 214.9 + 33.0, 324.9, places=10)

    def test_full_head_chain_fold_is_exact_for_many_head_angles(self):
        correction = homogeneous(rotation_y(NOMINAL_BH_PITCH))
        for yaw in (-1.0, -0.3, 0.0, 0.6, 1.0):
            for pitch in (-0.3, 0.0, 0.4, 0.8):
                with self.subTest(yaw=yaw, pitch=pitch):
                    original = chain(translate(5.6, 0.0, 77.0 + 214.9), homogeneous(rotation_z(yaw)),
                                     translate(0.0, 0.0, 33.0), homogeneous(rotation_y(pitch)),
                                     translate(*NOMINAL_CAMERA_MM), correction)
                    bhuman = chain(translate(5.6, 0.0, 324.9), homogeneous(rotation_z(yaw)),
                                   homogeneous(rotation_y(pitch)), translate(*NOMINAL_CAMERA_MM), correction)
                    self.assert_matrix_close(original, bhuman)

    def test_33mm_cannot_be_added_to_camera_offset_after_pitch(self):
        pitch = 0.5
        correct = chain(translate(0.0, 0.0, 33.0), homogeneous(rotation_y(pitch)), translate(*NOMINAL_CAMERA_MM))
        wrong = chain(homogeneous(rotation_y(pitch)), translate(NOMINAL_CAMERA_MM[0], NOMINAL_CAMERA_MM[1], NOMINAL_CAMERA_MM[2] + 33.0))
        delta = [wrong[n][3] - correct[n][3] for n in range(3)]
        self.assertAlmostEqual(delta[0], 33.0 * math.sin(pitch))
        self.assertAlmostEqual(delta[2], 33.0 * (math.cos(pitch) - 1.0))
        self.assertGreater(math.sqrt(sum(v * v for v in delta)), 16.0)

    def test_camera_rotation_applies_after_translation(self):
        # Optical axis conversion changes orientation, never the camera origin.
        camera = chain(translate(*NOMINAL_CAMERA_MM), homogeneous(rotation_y(NOMINAL_BH_PITCH)))
        self.assertEqual(tuple(row[3] for row in camera[:3]), NOMINAL_CAMERA_MM)
        rounded_template = chain(translate(*NOMINAL_CAMERA_MM), homogeneous(rotation_y(math.radians(15.159986))))
        self.assert_matrix_close(camera, rounded_template, 1e-8)


if __name__ == "__main__":
    print("Vendor NOMINAL K1/D455 geometry only; NOT per-robot calibration or motion acceptance.")
    unittest.main(verbosity=2)
