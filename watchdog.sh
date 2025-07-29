#!/bin/bash

# Путь к вашему Python-скрипту и конфигурационному файлу
APP_COMMAND="python3 run_lid_detector.py configs/config_lids_app.txt"
LOG_FILE="deepstream_app.log"

# Бесконечный цикл для перезапуска
while true; do
    echo "WATCHDOG: Запуск приложения... $(date)" | tee -a $LOG_FILE
    
    # Запускаем приложение и перенаправляем его вывод в лог-файл
    $APP_COMMAND >> $LOG_FILE 2>&1 &
    
    # Сохраняем PID процесса
    APP_PID=$!
    
    # Ждем завершения процесса
    wait $APP_PID
    
    echo "WATCHDOG: Приложение остановлено/упало с кодом $?. Перезапуск через 5 секунд..." | tee -a $LOG_FILE
    sleep 5
done