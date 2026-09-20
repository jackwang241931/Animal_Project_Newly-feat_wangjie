import os

import joblib
import numpy as np
from scipy.stats import linregress




def _extract_features(o2_seg, press_seg):
    o2_seg = np.array(o2_seg, dtype=float)
    press_seg = np.array(press_seg, dtype=float)

    if len(o2_seg) != 15 or len(press_seg) != 15:
        raise ValueError("输入数组必须正好包含15个元素")

    times = np.arange(1, 16)

    o2_mean = np.mean(o2_seg)
    o2_std = np.std(o2_seg)
    o2_min = np.min(o2_seg)
    o2_max = np.max(o2_seg)
    o2_slope, _, _, _, _ = linregress(times, o2_seg)

    press_mean = np.mean(press_seg)
    press_std = np.std(press_seg)
    press_min = np.min(press_seg)
    press_max = np.max(press_seg)
    press_slope, _, _, _, _ = linregress(times, press_seg)

    features = [o2_mean, o2_std, o2_min, o2_max, o2_slope,
                press_mean, press_std, press_min, press_max, press_slope]
    return np.nan_to_num(features, nan=0.0)


def predict_steady_o2(o2_array, pressure_array, is_reference=False, calibration_factor=None):
    MODEL_PATH =os.getcwd() +'./model/o2_steady_predictor_model_15s.pkl'

    model = joblib.load(MODEL_PATH)
    features = _extract_features(o2_array, pressure_array)
    predicted = model.predict([features])[0]

    if is_reference:
        if predicted == 0:
            raise ValueError("参考气预测值为0，无法计算比例")
        factor = 20.95 / predicted
        return predicted, factor
    else:
        if calibration_factor is None:
            raise ValueError("对于非参考气，必须提供calibration_factor")
        calibrated = predicted * calibration_factor
        return calibrated


if __name__ == "__main__":
    REFERENCE_O2 = np.array([
        17.1384, 17.1438, 17.1875, 17.2517, 17.2908,
        17.3099, 17.3099, 17.3099, 17.2995, 17.3020,
        17.2950, 17.2932, 17.2952, 17.2868, 17.2868
    ])

    REFERENCE_PRESSURE = np.array([
        80.4, 81.8, 81.2, 80.7, 80.5,
        80.1, 80.0, 80.1, 79.9, 80.0,
        79.9, 79.9, 79.9, 79.9, 79.8
    ])
    sample_o2 = np.array([17.1817, 17.1849, 17.1862, 17.1812, 17.1853, 17.1747,
                          17.1677, 17.1659, 17.1675, 17.1671, 17.1646, 17.1659,
                          17.1659, 17.1652, 17.1666])

    sample_press = np.array([79.9, 80.6, 80.4, 80.2, 80.1, 80.0, 79.9, 79.9,
                             80.0, 80.0, 80.0, 79.9, 79.9, 79.9, 79.9])

    ref_pred, factor = predict_steady_o2(REFERENCE_O2, REFERENCE_PRESSURE, is_reference=True)
    print(f"参考气原始预测: {ref_pred:.6f}")
    print(f"计算比例: {factor:.6f}")

    sample_pred = predict_steady_o2(sample_o2, sample_press, is_reference=False, calibration_factor=factor)
    print(f"样本校准预测: {sample_pred:.6f}")
