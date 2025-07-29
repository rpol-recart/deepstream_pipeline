import sys
import gi
import time # <-- Добавлено

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
import pyds

# --- КОНФИГУРАЦИЯ ЛОГИКИ ---
NUM_LIDS_PER_CAMERA = 8
LID_STATES = {i: "unknown" for i in range(1, 17)}
LID_STATUS_BUFFER = {i: [] for i in range(1, 17)}
STATUS_DEBOUNCE_COUNT = 5
CLASS_NAMES = {
    0: "lid_open",
    1: "lid_closed",
    2: "person",
    3: "object_foreign"
}

# --- НОВЫЙ БЛОК: КОНФИГУРАЦИЯ FPS МОНИТОРА ---
FPS_REPORT_INTERVAL_SEC = 10  # Как часто проверяем FPS (в секундах)
MIN_FPS_THRESHOLD = 5         # Минимальный допустимый FPS. Если ниже - перезапуск
FPS_COUNTERS = {0: 0, 1: 0}    # Счетчики кадров для source_id 0 и 1
LAST_FPS_CHECK_TIME = time.time()
# ... (все импорты и глобальные переменные остаются)

# --- НОВЫЕ КОНСТАНТЫ для читаемости ---
LID_GIE_UNIQUE_ID = 20
NEW_MODEL_GIE_UNIQUE_ID = 21

# Новые названия классов для второй модели
NEW_MODEL_CLASS_NAMES = {
    0: "smoke",
    1: "no_smoke"
}

# Функция-проба, которая теперь обрабатывает результаты от ДВУХ моделей
def probe_logic_callback(pad, info, u_data):
    # u_data теперь будет содержать объект GObject.MainLoop для остановки
    main_loop = u_data

    # --- ЛОГИКА ПРОВЕРКИ FPS ---
    global LAST_FPS_CHECK_TIME, FPS_COUNTERS
    current_time = time.time()
    
    # Проверяем FPS каждые FPS_REPORT_INTERVAL_SEC секунд
    if current_time - LAST_FPS_CHECK_TIME > FPS_REPORT_INTERVAL_SEC:
        elapsed_time = current_time - LAST_FPS_CHECK_TIME
        print("\n--- FPS Report ---")
        for source_id, frame_count in FPS_COUNTERS.items():
            fps = frame_count / elapsed_time
            print(f"Source [{source_id}] FPS: {fps:.2f}")

            # КРИТИЧЕСКАЯ ПРОВЕРКА
            if fps < MIN_FPS_THRESHOLD:
                print(f"!!! CRITICAL: FPS for source {source_id} is below threshold ({MIN_FPS_THRESHOLD}). Exiting for restart. !!!")
                main_loop.quit() # Корректно останавливаем главный цикл
                return Gst.PadProbeReturn.OK # Выходим из функции

        # Сбрасываем счетчики для следующего интервала
        FPS_COUNTERS = {0: 0, 1: 0}
        LAST_FPS_CHECK_TIME = current_time
        print("--------------------\n")

    
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Не удалось получить gst_buffer")
        return Gst.PadProbeReturn.OK
    
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        # ... (код получения frame_meta, source_id, инкремента FPS_COUNTERS) ...
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration: break
        source_id = frame_meta.source_id
        FPS_COUNTERS[source_id] += 1
        
        l_obj = frame_meta.obj_meta_list
        detections_in_rois = {}

        while l_obj is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
                
                # --- ГЛАВНОЕ ИЗМЕНЕНИЕ ЛОГИКИ ---
                # Теперь мы итерируемся по результатам классификации (метаданным от GIE)
                # а не по user_meta. Это надежнее.
                l_class = obj_meta.classifier_meta_list
                while l_class is not None:
                    try:
                        class_meta = pyds.NvDsClassifierMeta.cast(l_class.data)
                        
                        # Определяем, от какой модели пришел результат
                        if class_meta.unique_id == LID_GIE_UNIQUE_ID:
                            # --- Это результат от модели крышек ---
                            prep_meta = pyds.NvDsPreProcessObjectMeta.cast(obj_meta.parent.custom_meta_list.data)
                            roi_id = prep_meta.roi_index
                            # Применяем логику для крышек
                            l_label = class_meta.label_info_list
                            while l_label is not None:
                                try:
                                    label_info = pyds.NvDsLabelInfo.cast(l_label.data)
                                    class_name = CLASS_NAMES.get(label_info.class_id, "unknown_class")
                                    if class_name == "lid_open":
                                        detections_in_rois[roi_id] = "open"
                                    # ... (остальная логика приоритетов для крышек)
                                    l_label = l_label.next
                                except StopIteration: break
                        
                        elif class_meta.unique_id == NEW_MODEL_GIE_UNIQUE_ID:
                            # --- Это результат от НОВОЙ модели ---
                            prep_meta = pyds.NvDsPreProcessObjectMeta.cast(obj_meta.parent.custom_meta_list.data)
                            roi_id = prep_meta.roi_index # 0 или 1 для этой группы
                            
                            l_label = class_meta.label_info_list
                            while l_label is not None:
                                try:
                                    label_info = pyds.NvDsLabelInfo.cast(l_label.data)
                                    class_name = NEW_MODEL_CLASS_NAMES.get(label_info.class_id, "unknown_class")
                                    # !!! ЗДЕСЬ ВАША ЛОГИКА ДЛЯ НОВОЙ МОДЕЛИ !!!
                                    # Например, просто выведем в консоль
                                    print(f"EVENT (New Model): Source {source_id}, ROI {roi_id} -> Detected '{class_name}' with confidence {label_info.result}")
                                    l_label = l_label.next
                                except StopIteration: break
                        
                        l_label = None
                        l_class = l_class.next
                    except StopIteration: break
                l_class = None
            except StopIteration: break
            l_obj = l_obj.next
        l_obj = None

        update_lid_states(source_id, detections_in_rois)
        
        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
        display_meta.num_labels = 1
        py_nvosd_text_params = display_meta.text_params[0]
        display_text = []
        start_lid = (source_id * NUM_LIDS_PER_CAMERA) + 1
        end_lid = start_lid + NUM_LIDS_PER_CAMERA
        for i in range(start_lid, end_lid):
            display_text.append(f"Lid {i}: {LID_STATES[i]}")
        py_nvosd_text_params.display_text = " | ".join(display_text)
        py_nvosd_text_params.x_offset = 10; py_nvosd_text_params.y_offset = 12
        py_nvosd_text_params.font_params.font_name = "Serif"; py_nvosd_text_params.font_params.font_size = 12
        py_nvosd_text_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
        py_nvosd_text_params.set_bg_clr = 1; py_nvosd_text_params.text_bg_clr.set(0.0, 0.0, 0.0, 0.6)
        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)

        try:
            l_frame = l_frame.next
        except StopIteration: break
    
    return Gst.PadProbeReturn.OK



def update_lid_states(source_id, detections):
    # ... (эта функция остается без изменений) ...
    for roi_id in range(NUM_LIDS_PER_CAMERA):
        global_lid_id = (source_id * NUM_LIDS_PER_CAMERA) + roi_id + 1
        current_status = detections.get(roi_id, "closed")
        buffer = LID_STATUS_BUFFER[global_lid_id]
        buffer.append(current_status)
        if len(buffer) > STATUS_DEBOUNCE_COUNT:
            buffer.pop(0)
        if len(buffer) == STATUS_DEBOUNCE_COUNT and len(set(buffer)) == 1:
            new_stable_status = buffer[0]
            if LID_STATES[global_lid_id] != new_stable_status:
                LID_STATES[global_lid_id] = new_stable_status
                print(f"EVENT: Lid {global_lid_id} state changed to {new_stable_status}")


def main(args):
    GObject.threads_init()
    Gst.init(None)

    pipeline = Gst.parse_launch(f"nvinferbin config-file-path={args[1]}",)
    if not pipeline:
        print("Ошибка: не удалось создать пайплайн")
        sys.exit(1)

    osdsink = pipeline.get_by_name("sink0")
    if not osdsink:
        print("Ошибка: не удалось найти элемент 'sink0'")
        sys.exit(1)

    osd_pad = osdsink.get_static_pad("sink")
    if not osd_pad:
        print("Ошибка: не удалось получить sink pad у OSD")
        sys.exit(1)
    
    # Создаем главный цикл GObject
    loop = GObject.MainLoop()

    # --- ИЗМЕНЕНИЕ: передаем 'loop' в callback ---
    # Это позволит нам остановить пайплайн изнутри пробы
    osd_pad.add_probe(Gst.PadProbeType.BUFFER, probe_logic_callback, loop)

    print("Запуск пайплайна...")
    pipeline.set_state(Gst.State.PLAYING)

    try:
        # Запускаем главный цикл
        loop.run()
    except KeyboardInterrupt: # Добавлено для корректной остановки по Ctrl+C
        pass
    except Exception as e:
        print(f"Произошла ошибка: {e}")

    print("Остановка пайплайна")
    pipeline.set_state(Gst.State.NULL)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Использование: python3 run_lid_detector.py <путь_к_config_lids_app.txt>")
        sys.exit(1)
    sys.exit(main(sys.argv))