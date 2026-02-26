# Jenkins Setup Guide – HIL Test Automation (A → Z)

> Hướng dẫn cài đặt Jenkins Master + Slave (Agent) cho pipeline HIL test automation trên Windows.
> Cuối tài liệu có **Checklist Customize** mapping từng file cần update khi apply vào môi trường thực tế.

---

## Mục lục

1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt Jenkins Master](#2-cài-đặt-jenkins-master)
3. [Cấu hình Jenkins Master](#3-cấu-hình-jenkins-master)
4. [Cài đặt Jenkins Agent (Slave)](#4-cài-đặt-jenkins-agent-slave)
5. [Kết nối Agent → Master](#5-kết-nối-agent--master)
6. [Cài đặt Plugins](#6-cài-đặt-plugins)
7. [Tạo Pipeline Job](#7-tạo-pipeline-job)
8. [Multi-Variant Setup (2 Jobs)](#8-multi-variant-setup)
9. [Credentials & Security](#9-credentials--security)
10. [Email Notification](#10-email-notification)
11. [Checklist Customize cho dự án](#11-checklist-customize-cho-dự-án)

---

## 1. Yêu cầu hệ thống

### Jenkins Master (1 máy)

| Thành phần | Yêu cầu |
|---|---|
| OS | Windows 10/11 hoặc Windows Server 2019+ |
| Java | JDK 17+ (recommend Eclipse Temurin) |
| RAM | ≥ 4 GB |
| Disk | ≥ 50 GB |
| Network | Có thể truy cập từ tất cả Agent |
| Port | 8080 (Web UI), 50000 (Agent JNLP) |

### Jenkins Agent / Slave (N máy = N bench)

| Thành phần | Yêu cầu |
|---|---|
| OS | Windows 10/11 (cùng version với bench HIL) |
| Java | JRE 17+ |
| Python | 3.9+ (cho Robot Framework + scripts) |
| Robot Framework | `pip install robotframework` |
| Git | Git for Windows |
| Network | Kết nối được tới Master (port 50000) |
| Hardware | Kết nối HW bench (CAN, Power Supply, ECU) |

---

## 2. Cài đặt Jenkins Master

### 2.1 Cài Java

```powershell
# Download & install Eclipse Temurin JDK 17
winget install EclipseAdoptium.Temurin.17.JDK

# Verify
java -version
```

### 2.2 Cài Jenkins

```powershell
# Option 1: Windows Installer (recommend)
# Download: https://www.jenkins.io/download/
# Chạy jenkins.msi → cài đặt như Windows Service

# Option 2: WAR file (flexible)
# Download jenkins.war từ https://www.jenkins.io/download/
java -jar jenkins.war --httpPort=8080
```

### 2.3 Initial Setup

1. Mở browser → `http://localhost:8080`
2. Lấy initial admin password:
   ```powershell
   type "C:\ProgramData\Jenkins\.jenkins\secrets\initialAdminPassword"
   ```
3. Chọn **"Install suggested plugins"**
4. Tạo Admin user:
   - Username: `admin`
   - Password: (đặt password mạnh)
5. Set Jenkins URL: `http://<MASTER_IP>:8080/`

### 2.4 Cấu hình Jenkins Service (Windows)

```powershell
# Mở Services (services.msc)
# Tìm "Jenkins" → Properties:
#   - Startup type: Automatic
#   - Log On: Local System (hoặc service account có quyền)

# Hoặc qua PowerShell:
Set-Service -Name "Jenkins" -StartupType Automatic
```

---

## 3. Cấu hình Jenkins Master

### 3.1 System Configuration

**Manage Jenkins → System**

| Setting | Giá trị | Ghi chú |
|---|---|---|
| Jenkins URL | `http://<MASTER_IP>:8080/` | Phải match IP thực tế |
| # of executors | `0` | Master KHÔNG chạy build, chỉ orchestrate |
| Quiet period | `5` | Seconds |
| SCM checkout retry | `3` | |

### 3.2 Global Tool Configuration

**Manage Jenkins → Tools**

```
Git:
  Name: Default
  Path: C:\Program Files\Git\bin\git.exe

JDK:
  Name: JDK17
  JAVA_HOME: C:\Program Files\Eclipse Adoptium\jdk-17...

Python (custom tool):
  Name: Python3
  Path: C:\Python39\python.exe
```

### 3.3 Mở JNLP Port cho Agent

**Manage Jenkins → Security → Agents**

| Setting | Giá trị |
|---|---|
| TCP port for inbound agents | Fixed: `50000` |
| Agent protocols | Inbound TCP Agent Protocol/2 (Ping) ✅ |

> [!IMPORTANT]
> Phải mở port 50000 trên firewall của Master:
> ```powershell
> netsh advfirewall firewall add rule name="Jenkins Agent" dir=in action=allow protocol=TCP localport=50000
> ```

---

## 4. Cài đặt Jenkins Agent (Slave)

> Lặp lại bước này cho **MỖI bench HIL** (bench-1, bench-2, ..., bench-5).

### 4.1 Cài Prerequisites trên Agent

```powershell
# 1. Java
winget install EclipseAdoptium.Temurin.17.JRE

# 2. Python + Robot Framework
winget install Python.Python.3.9
pip install robotframework
pip install robotframework-seleniumlibrary  # nếu cần

# 3. Git
winget install Git.Git

# 4. Project dependencies
pip install -r requirements.txt
```

### 4.2 Tạo Node trên Master

**Manage Jenkins → Nodes → New Node**

| Field | Giá trị (ví dụ bench-1) | Ghi chú |
|---|---|---|
| Node name | `hil-bench-1` | **PHẢI match `getBenchLabels()` trong Jenkinsfile** |
| Type | Permanent Agent | |
| # of executors | `1` | 1 bench = 1 executor (tránh conflict HW) |
| Remote root directory | `C:\jenkins-agent` | Working directory trên agent |
| Labels | `hil-bench-1 hil-agent` | Label dùng để route jobs |
| Usage | Only build jobs with matching labels | |
| Launch method | Launch agent by connecting to controller | JNLP |

> [!WARNING]
> **Executors = 1** rất quan trọng! Mỗi bench chỉ chạy 1 test suite tại 1 thời điểm vì HW (CAN, Power supply) không share được.

### 4.3 Tạo thêm Labels cho bench đặc biệt

Nếu bench có hardware đặc biệt (eCall modem, special relay):

```
hil-bench-1 hil-agent hil-ecall        ← bench chạy eCall
hil-bench-5 hil-agent hil-special      ← bench có special env HW
```

---

## 5. Kết nối Agent → Master

### 5.1 Download Agent JAR

Trên **MỖI máy agent**, tải `agent.jar`:

```powershell
# Tạo folder
mkdir C:\jenkins-agent
cd C:\jenkins-agent

# Download agent.jar từ Master
Invoke-WebRequest -Uri "http://<MASTER_IP>:8080/jnlpJars/agent.jar" -OutFile agent.jar
```

### 5.2 Lấy Agent Secret

1. Trên Master: **Manage Jenkins → Nodes → hil-bench-1**
2. Copy secret token (chuỗi dài hex)

### 5.3 Chạy Agent

```powershell
java -jar agent.jar ^
    -url http://<MASTER_IP>:8080/ ^
    -secret <SECRET_TOKEN> ^
    -name hil-bench-1 ^
    -workDir C:\jenkins-agent
```

### 5.4 Cài Agent như Windows Service (auto-start)

```powershell
# Option 1: NSSM (recommend)
# Download NSSM: https://nssm.cc/download
nssm install JenkinsAgent "C:\Program Files\Eclipse Adoptium\jre-17\bin\java.exe" ^
    "-jar C:\jenkins-agent\agent.jar -url http://<MASTER_IP>:8080/ -secret <SECRET> -name hil-bench-1 -workDir C:\jenkins-agent"
nssm set JenkinsAgent AppDirectory C:\jenkins-agent
nssm set JenkinsAgent Start SERVICE_AUTO_START
nssm start JenkinsAgent
```

```powershell
# Option 2: Windows Task Scheduler
# Tạo task chạy khi startup:
$action = New-ScheduledTaskAction `
    -Execute "java" `
    -Argument "-jar C:\jenkins-agent\agent.jar -url http://<MASTER_IP>:8080/ -secret <SECRET> -name hil-bench-1 -workDir C:\jenkins-agent" `
    -WorkingDirectory "C:\jenkins-agent"

$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -DontStopOnIdleEnd -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "JenkinsAgent" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

### 5.5 Verify Connection

- Master UI: **Manage Jenkins → Nodes** → check agent status = 🟢 Online
- Agent log: `C:\jenkins-agent\remoting\logs\remoting.log`

---

## 6. Cài đặt Plugins

**Manage Jenkins → Plugins → Available plugins**

| Plugin | Mục đích |
|---|---|
| **Pipeline** | Pipeline as Code (Jenkinsfile) |
| **Pipeline: Stage View** | Visualize stages |
| **Git** | SCM checkout |
| **Robot Framework** | Parse Robot output.xml |
| **Email Extension** | Rich email notifications |
| **Timestamper** | Add timestamps to console output |
| **Workspace Cleanup** | Clean workspace |
| **Build Timeout** | Pipeline timeout |
| **Pipeline Utility Steps** | `readJSON`, `writeFile`, etc. |
| **Credentials Binding** | Secure credentials in pipeline |
| **Matrix Authorization** | Fine-grained permissions |
| **OWASP Markup Formatter** | Safe HTML in build descriptions |

```groovy
// Verify sau khi cài xong (Script Console):
println Jenkins.instance.pluginManager.plugins.collect { "${it.shortName} (${it.version})" }.sort().join('\n')
```

---

## 7. Tạo Pipeline Job

### 7.1 Tạo Job chính

**New Item → Pipeline**

| Setting | Giá trị |
|---|---|
| Name | `HIL_Variant_A_Nightly` |
| Type | Pipeline |
| Description | Nightly HIL test run for Variant A |
| This project is parameterized | ✅ (params từ Jenkinsfile) |
| Pipeline → Definition | **Pipeline script from SCM** |
| SCM | Git |
| Repository URL | `https://git.company.com/hil-tests.git` |
| Branch | `*/main` |
| Script Path | `cicd/Jenkinsfile` |

### 7.2 Check SCM Triggers

Trong job config → **Build Triggers**:

| Trigger | Giá trị |
|---|---|
| Build periodically | `H 20 * * 1-5` (8PM Mon-Fri) |
| Poll SCM | (optional) `H/15 * * * *` |

> [!NOTE]
> Cron trong Jenkinsfile (`triggers { cron(...) }`) sẽ override job config sau lần chạy đầu tiên.

### 7.3 Run lần đầu

1. **Build with Parameters**
2. Set `VARIANT = Variant_A`, `TEST_SCOPE = smoke` (chạy nhỏ trước)
3. Build → check console output
4. Fix issues nếu có

---

## 8. Multi-Variant Setup

Chạy **2 variants** song song bằng 2 Jenkins jobs cùng Jenkinsfile:

### Job 1: Variant A

| Parameter | Giá trị |
|---|---|
| Name | `HIL_Variant_A_Nightly` |
| VARIANT | `Variant_A` |
| BENCH_ALLOCATION | `hil-bench-1,hil-bench-2` |
| ECALL_BENCH | `hil-bench-1` |
| Trigger cron | `H 20 * * 1-5` |

### Job 2: Variant B

| Parameter | Giá trị |
|---|---|
| Name | `HIL_Variant_B_Nightly` |
| VARIANT | `Variant_B` |
| BENCH_ALLOCATION | `hil-bench-3,hil-bench-4,hil-bench-5` |
| ECALL_BENCH | `hil-bench-3` |
| Trigger cron | `H 20 * * 1-5` |

### Job 3: Weekly Report (Friday)

| Parameter | Giá trị |
|---|---|
| Name | `HIL_Weekly_Report` |
| Trigger cron | `H 18 * * 5` (Friday 6PM) |
| Pipeline script | (inline script gọi `failure_classifier.py weekly-report`) |

---

## 9. Credentials & Security

### 9.1 Git Credentials

**Manage Jenkins → Credentials → Global → Add Credentials**

| Field | Giá trị |
|---|---|
| Kind | Username with password |
| Username | git username |
| Password | personal access token |
| ID | `git-hil-repo` |

### 9.2 Pipeline sử dụng credential

```groovy
// Trong Jenkinsfile:
checkout([$class: 'GitSCM',
    branches: [[name: '*/main']],
    userRemoteConfigs: [[
        url: 'https://git.company.com/hil-tests.git',
        credentialsId: 'git-hil-repo'
    ]]
])
```

### 9.3 Security Hardening

**Manage Jenkins → Security**

| Setting | Giá trị |
|---|---|
| Security Realm | Jenkins' own user database |
| Authorization | Matrix-based security |
| CSRF Protection | ✅ Enabled |
| Agent → Controller Access | ✅ Restrict |

---

## 10. Email Notification

### 10.1 SMTP Configuration

**Manage Jenkins → System → E-mail Notification**

| Setting | Ví dụ |
|---|---|
| SMTP server | `smtp.company.com` |
| SMTP port | `587` |
| Use TLS | ✅ |
| SMTP Username | `jenkins@company.com` |
| SMTP Password | (credential) |
| Default suffix | `@company.com` |

### 10.2 Extended E-mail

**Manage Jenkins → System → Extended E-mail Notification**

| Setting | Giá trị |
|---|---|
| SMTP server | (same as above) |
| Default Content Type | HTML |
| Default Recipients | `team@company.com` |
| Default Subject | `[HIL] $PROJECT_NAME - Build #$BUILD_NUMBER - $BUILD_STATUS` |

---

## 11. Checklist Customize cho dự án

> [!CAUTION]
> Các mục bên dưới **BẮT BUỘC** phải update khi apply vào môi trường thực tế. Nếu không, pipeline sẽ không chạy được.

### 11.1 Jenkinsfile

| Dòng / Function | Cần update | Ví dụ |
|---|---|---|
| `getBenchLabels()` | Danh sách agent labels match Jenkins Nodes | `['hil-bench-1', 'hil-bench-2']` |
| `ECALL_BENCH` default | Bench stable nhất cho eCall | `hil-bench-1` |
| `TEST_DIR` | Đường dẫn folder chứa test scripts | `tests` hoặc `test_suites` |
| `VARIANT` choices | List variants thực tế | `['ModelX_LHD', 'ModelX_RHD']` |
| `ROBOT_OPTS` | Variables cần truyền vào Robot | `--variable ECU_IP:192.168.1.10` |
| Email recipients | Địa chỉ team thực tế | `hil-team@company.com` |
| Cron trigger | Giờ chạy phù hợp timezone | `H 22 * * 1-5` (10PM) |
| `agent { label '...' }` | Label match agent thực tế | `hil-bench-1` |

### 11.2 env_validation.robot

| Test Case | Cần update | Mô tả |
|---|---|---|
| `ENV_CHECK_02` | IP/hostname ECU | `192.168.1.10` → IP thật |
| `ENV_CHECK_03` | CAN interface name | `Vector XL` → tên driver thật |
| `ENV_CHECK_04` | COM port relay | `COM3` → port thật |
| `ENV_CHECK_05` | Paths config files | DBC, ARXML paths thật |

### 11.3 tcid_mapper.py

| Function | Cần update khi |
|---|---|
| `_extract_tcid()` regex | Naming convention khác `TCID_Name.robot` |
| `_get_feature()` | Folder structure khác (multi-level nesting) |

### 11.4 failure_classifier.py

| Section | Cần update |
|---|---|
| `ENV_FAILURE_PATTERNS` | Thêm error messages thực tế từ log bench |
| `FLAKY_PATTERNS` | Thêm patterns cho known flaky tests |
| eCall patterns | Điều chỉnh theo modem/IVS vendor thực tế |

### 11.5 test_runner_wrapper.py

| Parameter | Default | Cần xem xét |
|---|---|---|
| `HANG_TIMEOUT` | 300s (5min) | Tăng nếu TC chậm (HW boot lâu) |
| `TEST_TIMEOUT` | 600s (10min) | Tăng cho TC integration phức tạp |

### 11.6 Quick Start Checklist

```
□ 1. Cài Jenkins Master + mở port 8080, 50000
□ 2. Cài plugins (Section 6)
□ 3. Cài Java + Python + Git trên MỖI bench
□ 4. Tạo Nodes: hil-bench-1..N (executors=1)
□ 5. Kết nối agents → verify 🟢 Online
□ 6. Tạo Git credential
□ 7. Update getBenchLabels() trong Jenkinsfile
□ 8. Update env_validation.robot (IP, COM, paths)
□ 9. Tạo Pipeline job → SCM pointing to repo
□ 10. Chạy thử: TEST_SCOPE=smoke
□ 11. Setup email notification
□ 12. Tạo cron trigger cho nightly
□ 13. (Optional) Tạo Job 2 cho Variant B
□ 14. (Optional) Tạo Weekly Report job
```
