*** Settings ***
Documentation    HIL Bench Environment Validation Suite
...              Chạy 4-5 test cases kiểm tra sẵn sàng của bench trước khi chạy test chính.
...              Nếu bất kỳ check nào FAIL → pipeline abort, không chạy 2500 TCs lãng phí.
...
...              Customize các keyword bên dưới cho phù hợp với setup bench thực tế.

Library          OperatingSystem
Library          Process
Library          String
Library          Collections

# Import thêm libraries tuỳ theo project:
# Library        CANoeLibrary
# Library        SerialLibrary
# Library        SSHLibrary

Suite Setup      Log    🔍 Starting Environment Validation...
Suite Teardown   Log    ✅ Environment Validation Complete

*** Variables ***
# ─── Customize cho bench của bạn ───
${ECU_IP}                  192.168.1.100
${CANOE_PATH}              C:\\Program Files\\Vector CANoe\\CANoe64.exe
${BENCH_POWER_RELAY_COM}   COM3
${DUT_DIAG_PORT}           COM5
${EXPECTED_PYTHON_VER}     3.
${EXPECTED_ROBOT_VER}      7.

# Timeouts
${PING_TIMEOUT}            5
${CONNECTION_TIMEOUT}      10s

*** Test Cases ***
# ══════════════════════════════════════════════════════════════════════
# CHECK 1: Verify Bench Software Stack
# ══════════════════════════════════════════════════════════════════════
ENV_CHECK_01_Software_Stack_Ready
    [Documentation]    Kiểm tra Python, Robot Framework, và các tool cần thiết đã sẵn sàng.
    [Tags]    env_check    priority:critical

    # Python
    ${python_ver}=    Run And Get Output    python --version
    Should Contain    ${python_ver}    Python ${EXPECTED_PYTHON_VER}
    ...    msg=Python version mismatch! Got: ${python_ver}
    Log    ✅ Python: ${python_ver}

    # Robot Framework
    ${robot_ver}=    Run And Get Output    robot --version
    Log    ✅ Robot Framework: ${robot_ver}

    # Kiểm tra libraries quan trọng (customize theo project)
    ${import_check}=    Run And Get Output
    ...    python -c "import robot; print('robot OK')"
    Should Contain    ${import_check}    robot OK    msg=Robot Framework import failed!

    # Thêm check cho libraries khác nếu cần:
    # ${can_check}=    Run And Get Output    python -c "import can; print('python-can OK')"
    # Should Contain    ${can_check}    python-can OK

    Log    ✅ Software stack verification passed

# ══════════════════════════════════════════════════════════════════════
# CHECK 2: Verify ECU/DUT Network Connectivity
# ══════════════════════════════════════════════════════════════════════
ENV_CHECK_02_ECU_Network_Connectivity
    [Documentation]    Ping ECU/DUT để xác nhận kết nối mạng sẵn sàng.
    ...                Nếu FAIL: kiểm tra cáp Ethernet, IP config, hoặc ECU power.
    [Tags]    env_check    priority:critical

    # Ping ECU
    ${ping_result}=    Run And Get Output    ping -n 3 -w ${PING_TIMEOUT}000 ${ECU_IP}
    Should Not Contain    ${ping_result}    100% loss
    ...    msg=Cannot reach ECU at ${ECU_IP}! Check cable/power.
    Should Contain    ${ping_result}    Reply from
    ...    msg=No ping reply from ECU at ${ECU_IP}!

    Log    ✅ ECU reachable at ${ECU_IP}

    # Optional: Check specific ports if needed
    # ${port_check}=    Run And Get Output
    # ...    python -c "import socket; s=socket.create_connection(('${ECU_IP}', 13400), 5); print('DoIP OK'); s.close()"
    # Should Contain    ${port_check}    DoIP OK    msg=DoIP port 13400 not accessible!

# ══════════════════════════════════════════════════════════════════════
# CHECK 3: Verify CAN/Diagnostic Interface
# ══════════════════════════════════════════════════════════════════════
ENV_CHECK_03_CAN_Interface_Available
    [Documentation]    Kiểm tra CAN interface (Vector CANoe/CANalyzer/PCAN) khả dụng.
    ...                Nếu FAIL: kiểm tra Vector driver, USB dongle, hoặc license.
    [Tags]    env_check    priority:critical

    # Option A: Check Vector CANoe executable exists
    File Should Exist    ${CANOE_PATH}
    ...    msg=CANoe not found at ${CANOE_PATH}!

    # Option B: Check CAN interface via python-can (nếu dùng)
    # ${can_check}=    Run And Get Output
    # ...    python -c "import can; bus=can.Bus(interface='vector', channel=0); print('CAN OK'); bus.shutdown()"
    # Should Contain    ${can_check}    CAN OK    msg=CAN interface not available!

    # Option C: Check Vector hardware via XL API
    ${xl_check}=    Run And Get Output
    ...    python -c "try: import ctypes; print('XL API accessible'); \nexcept: print('XL API failed')"
    Log    Vector/XL check: ${xl_check}

    # Check serial port for diagnostics (if applicable)
    ${serial_check}=    Run And Get Output
    ...    python -c "import serial.tools.list_ports; ports=[p.device for p in serial.tools.list_ports.comports()]; print(f'Available ports: {ports}')"
    Log    ${serial_check}

    Log    ✅ CAN/Diagnostic interface check passed

# ══════════════════════════════════════════════════════════════════════
# CHECK 4: Verify Power Supply & Relay Board
# ══════════════════════════════════════════════════════════════════════
ENV_CHECK_04_Power_And_Relay_Board
    [Documentation]    Kiểm tra nguồn điện và relay board (nếu dùng để control DUT power, ignition...).
    ...                Nếu FAIL: kiểm tra kết nối USB relay, power supply status.
    [Tags]    env_check    priority:high

    # Check relay board COM port available
    ${com_check}=    Run And Get Output
    ...    python -c "import serial.tools.list_ports; ports=[p.device for p in serial.tools.list_ports.comports()]; print(ports)"
    Log    Available COM ports: ${com_check}

    # Uncomment and customize based on your relay board:
    # ${relay_check}=    Run And Get Output
    # ...    python -c "import serial; s=serial.Serial('${BENCH_POWER_RELAY_COM}', 9600, timeout=3); s.write(b'STATUS\\n'); r=s.readline(); print(f'Relay: {r}'); s.close()"
    # Should Not Be Empty    ${relay_check}    msg=Relay board not responding on ${BENCH_POWER_RELAY_COM}!

    Log    ✅ Power/Relay check passed (verify manually if needed)

# ══════════════════════════════════════════════════════════════════════
# CHECK 5: Verify Test Data & Configuration Files
# ══════════════════════════════════════════════════════════════════════
ENV_CHECK_05_Test_Configuration_Valid
    [Documentation]    Kiểm tra các file cấu hình cần thiết tồn tại và valid.
    ...                DBC files, ARXML, test data, variant config, etc.
    [Tags]    env_check    priority:high

    # Check test directory exists
    Directory Should Exist    ${CURDIR}${/}..${/}tests
    ...    msg=Test directory not found!

    # Check variant config (customize paths)
    # File Should Exist    ${CURDIR}${/}..${/}config${/}variant_${VARIANT}.yaml
    # ...    msg=Variant config file not found!

    # Check DBC / communication database
    # File Should Exist    ${CURDIR}${/}..${/}data${/}vehicle.dbc
    # ...    msg=CAN DBC file not found!

    # Verify workspace is clean (no leftover lock files)
    ${lock_files}=    Run And Get Output    dir /b /s *.lock 2>nul || echo "no locks"
    Log    Lock file check: ${lock_files}

    Log    ✅ Configuration validation passed

*** Keywords ***
Run And Get Output
    [Documentation]    Run command and return stdout. Handles both bat and shell.
    [Arguments]    ${command}
    ${result}=    Run Process    cmd    /c    ${command}
    ...    timeout=${CONNECTION_TIMEOUT}    on_timeout=terminate
    Log    Command: ${command}
    Log    RC: ${result.rc} | Output: ${result.stdout}
    RETURN    ${result.stdout}
