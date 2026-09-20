import numpy as np

from public.config_class.global_setting import global_setting


def convert_data_to_cage_format(original_data):
    columns = original_data['columns']
    columns_title = original_data['columns_title']
    column_mapping = dict(zip(columns, columns_title))
    reference_cage = int(global_setting.get_setting('configer')['mouse_cage']['reference'])

    time_grouped_data = {}

    for row in original_data['rows']:
        if row.get('time') is None:
            time_str = None
        else:
            time_str = row['time'].split('.')[0]

        if time_str not in time_grouped_data:
            time_grouped_data[time_str] = {
                "x_value": time_str,
                "cages": {}
            }

        cage_number = row['mouse_cage_number']
        cage_name = "参考笼" if cage_number == reference_cage else f"鼠笼{cage_number}"
        cage_data = {}

        for column, value in row.items():
            if column in ['id', 'mouse_cage_number', 'epoch_start_time', 'epoch_end_time', 'time', 'remarks']:
                continue

            if value is None or type(value) is str:
                value = np.nan
            chinese_name = column_mapping.get(column, column)
            cage_data[chinese_name] = value

        time_grouped_data[time_str]["cages"][cage_name] = cage_data

    return list(time_grouped_data.values())
