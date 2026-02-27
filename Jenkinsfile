// =============================================================================
// HIL Test Automation - Simplified Pipeline
// Framework: Robot Framework | CI: Jenkins
// =============================================================================
//
// Pipeline Flow (4 Stages):
//   Stage 1: Checkout & Env Check   → Pull code + validate bench environment
//   Stage 2: Run Test Scripts       → Execute tests with hang/abort detection
//   Stage 3: Analyze & Rerun        → Classify failures + retry env-fails
//   Stage 4: Report                 → Generate report + email results
//
// Early Abort (Fail-Fast):
//   Nếu 15 test liên tiếp FAIL hoặc fail rate >80% → dừng sớm tiết kiệm thời gian
// =============================================================================

pipeline {
    agent { label 'hil-bench' }

    parameters {
        // ─── ESSENTIAL ───
        string(
            name: 'TEST_FOLDER',
            defaultValue: '',
            description: 'Folder chứa test scripts (relative to tests/). Để trống = chạy toàn bộ. Ví dụ: eCall hoặc HVAC/SubFolder'
        )
        string(
            name: 'SW_VERSION',
            defaultValue: 'unknown',
            description: 'Phiên bản SW đang test (hiển thị trong report)'
        )
        string(
            name: 'EMAIL_RECIPIENTS',
            defaultValue: '',
            description: 'Người nhận email kết quả (comma-separated). Để trống = không gửi email'
        )

        // ─── SELECTION ───
        choice(
            name: 'SELECTION_MODE',
            choices: ['folder', 'tcid', 'full'],
            description: 'folder=dùng TEST_FOLDER, tcid=dùng TCID_LIST, full=chạy toàn bộ'
        )
        text(
            name: 'TCID_LIST',
            defaultValue: '',
            description: 'Danh sách TCID (1 per line hoặc comma-separated). Dùng khi SELECTION_MODE=tcid. Ví dụ: TC001,TC002,TC045'
        )

        // ─── OPTIONS ───
        booleanParam(
            name: 'SKIP_ENV_CHECK',
            defaultValue: false,
            description: 'Bỏ qua kiểm tra môi trường (dùng khi bench đã biết stable)'
        )
        string(
            name: 'MAX_RETRY',
            defaultValue: '2',
            description: 'Số lần retry tối đa cho test cases bị lỗi môi trường'
        )
        booleanParam(
            name: 'ENABLE_EARLY_ABORT',
            defaultValue: true,
            description: 'Tự động dừng sớm khi phát hiện mass failure (40 consecutive fails hoặc >80% fail rate sau 70 tests)'
        )
    }

    environment {
        RF_OUTPUT         = "results/${BUILD_NUMBER}"
        ROBOT_OPTS        = "--loglevel DEBUG"
        WRAPPER_SCRIPT    = "cicd/scripts/test_runner_wrapper.py"
        TRACKER_SCRIPT    = "cicd/scripts/realtime_tracker.py"
        CLASSIFIER_SCRIPT = "cicd/scripts/failure_classifier.py"
        TCID_MAPPER       = "cicd/scripts/tcid_mapper.py"
        ENV_CHECK_SUITE   = "cicd/env_check/env_validation.robot"
        TEST_DIR          = "tests"
        HANG_TIMEOUT      = "300"
        TEST_TIMEOUT      = "600"
    }

    options {
        timestamps()
        timeout(time: 12, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    stages {
        // =================================================================
        // STAGE 1: CHECKOUT & ENVIRONMENT CHECK
        // =================================================================
        stage('Checkout & Env Check') {
            steps {
                checkout scm

                echo """
                ╔══════════════════════════════════════════════════╗
                ║  HIL Test Pipeline                               ║
                ║  SW Version:  ${params.SW_VERSION}               ║
                ║  Mode:        ${params.SELECTION_MODE}           ║
                ║  Test Folder: ${params.TEST_FOLDER ?: '-'}       ║
                ║  Build:       #${BUILD_NUMBER}                   ║
                ╚══════════════════════════════════════════════════╝
                """

                script {
                    if (params.SKIP_ENV_CHECK) {
                        echo "⏭️ Skipping environment validation (SKIP_ENV_CHECK=true)"
                    } else {
                        echo "🔍 Running Environment Validation..."
                        try {
                            bat """
                                robot --outputdir ${RF_OUTPUT}/env_check ^
                                      --loglevel DEBUG ^
                                      ${ENV_CHECK_SUITE}
                            """
                            echo "✅ Environment validation PASSED"
                        } catch (Exception e) {
                            error("""
                                ❌ Environment validation FAILED!
                                ┌──────────────────────────────────────────┐
                                │  BENCH IS NOT READY FOR TESTING          │
                                │  Check: ECU power, CAN cable, licenses   │
                                │  Pipeline aborted to save time           │
                                └──────────────────────────────────────────┘
                            """)
                        }
                    }
                }
            }
        }

        // =================================================================
        // STAGE 2: RUN TEST SCRIPTS
        // =================================================================
        // - Sử dụng test_runner_wrapper.py: hang detection + early abort
        // - Realtime tracker ghi pass/fail từng test
        // - Selection: folder / tcid / full
        // - Early abort: 15 consecutive fails hoặc >80% fail → dừng sớm
        // =================================================================
        stage('Run Test Scripts') {
            steps {
                script {
                    // ─── Resolve test path based on SELECTION_MODE ───
                    def testPath = TEST_DIR
                    def extraArgs = ""

                    switch (params.SELECTION_MODE) {
                        case 'tcid':
                            if (params.TCID_LIST?.trim()) {
                                echo "🔢 TCID MODE: Resolving test cases by TCID..."
                                def tcidFile = "${RF_OUTPUT}/tcid_list.txt"
                                writeFile file: tcidFile, text: params.TCID_LIST

                                // Generate argfile from TCID list
                                bat """
                                    python ${TCID_MAPPER} generate-argfile ^
                                        --tcid-list "${params.TCID_LIST.trim().replaceAll('\n', ',')}" ^
                                        --test-dir ${TEST_DIR} ^
                                        --output ${RF_OUTPUT}/selection_argfile.txt
                                """
                                extraArgs = "--selection-argfile ${RF_OUTPUT}/selection_argfile.txt"
                                echo "✅ TCID resolution complete"
                            } else {
                                echo "⚠️ TCID_LIST is empty, falling back to full run"
                            }
                            break

                        case 'folder':
                            if (params.TEST_FOLDER?.trim()) {
                                testPath = "${TEST_DIR}/${params.TEST_FOLDER.trim()}"
                                echo "📁 FOLDER MODE: ${testPath}"
                            } else {
                                echo "⚠️ TEST_FOLDER is empty, running full test suite"
                            }
                            break

                        default: // 'full'
                            echo "📋 FULL MODE: Running all test cases"
                            break
                    }

                    def trackerFile = "${RF_OUTPUT}/tracker.json"

                    echo "🚀 Running tests: ${testPath}"
                    echo "🛡️ Hang detection: ${HANG_TIMEOUT}s | Test timeout: ${TEST_TIMEOUT}s"
                    // Early abort config
                    def abortArgs = ""
                    if (params.ENABLE_EARLY_ABORT) {
                        abortArgs = "--max-consecutive-fails 40 --fail-rate-abort 80"
                        echo "🛑 Early abort: ENABLED (40 consecutive fails or >80% fail rate after 70 tests)"
                    } else {
                        abortArgs = "--max-consecutive-fails 0 --fail-rate-abort 0"
                        echo "🛑 Early abort: DISABLED (sẽ chạy hết tất cả tests)"
                    }
                    echo "📊 Realtime tracking: ${trackerFile}"

                    try {
                        bat """
                            python ${WRAPPER_SCRIPT} ^
                                --suite ${testPath} ^
                                --outputdir ${RF_OUTPUT}/execution ^
                                --hang-timeout ${HANG_TIMEOUT} ^
                                --test-timeout ${TEST_TIMEOUT} ^
                                ${abortArgs} ^
                                --tracker-file ${trackerFile} ^
                                --bench-name ${env.NODE_NAME} ^
                                --feature-name ${params.TEST_FOLDER ?: 'full'} ^
                                ${extraArgs} ^
                                -- ${ROBOT_OPTS}
                        """
                    } catch (Exception e) {
                        echo "⚠️ Test execution completed with failures, hangs, or early abort"
                    }

                    // Print final tracker summary
                    try {
                        bat """
                            python ${TRACKER_SCRIPT} summary ^
                                --dir ${RF_OUTPUT}
                        """
                    } catch (Exception e) {
                        echo "⚠️ Tracker summary unavailable"
                    }
                }
            }
        }

        // =================================================================
        // STAGE 3: ANALYZE & RERUN
        // =================================================================
        stage('Analyze & Rerun') {
            steps {
                script {
                    echo "🔄 Analyzing failures and retrying if needed..."

                    // Find output.xml (may be in different dirs due to hang recovery)
                    def mainOutput = "${RF_OUTPUT}/execution/output.xml"
                    if (!fileExists(mainOutput)) {
                        mainOutput = "${RF_OUTPUT}/execution/merged/output.xml"
                    }
                    if (!fileExists(mainOutput)) {
                        mainOutput = "${RF_OUTPUT}/execution/run_1/output.xml"
                    }
                    if (!fileExists(mainOutput)) {
                        echo "❌ No output.xml found. Skipping analysis."
                        return
                    }

                    // Classify failures
                    def envFailCount = 0
                    try {
                        def countOutput = bat(
                            script: "python ${CLASSIFIER_SCRIPT} count-env-fail --output-xml ${mainOutput}",
                            returnStdout: true
                        ).trim()
                        envFailCount = countOutput.tokenize('\n').last().trim().toInteger()
                    } catch (Exception e) {
                        echo "⚠️ Failure classification error: ${e.message}"
                    }

                    echo "📊 Environment failures: ${envFailCount}"

                    if (envFailCount == 0) {
                        echo "✅ No environment failures to retry."
                        env.FINAL_OUTPUT = mainOutput
                        return
                    }

                    // Retry loop
                    def currentOutput = mainOutput
                    def maxRetry = params.MAX_RETRY.toInteger()

                    for (int attempt = 1; attempt <= maxRetry; attempt++) {
                        echo "🔄 Retry ${attempt}/${maxRetry} (${envFailCount} env failures)"

                        try {
                            bat """
                                robot --rerunfailed ${currentOutput} ^
                                      --outputdir ${RF_OUTPUT}/retry_${attempt} ^
                                      ${ROBOT_OPTS} ^
                                      ${TEST_DIR}
                            """
                        } catch (Exception e) {
                            echo "⚠️ Retry ${attempt} completed with some failures"
                        }

                        // Merge retry results
                        def mergedDir = "${RF_OUTPUT}/merged"
                        bat """
                            rebot --merge ^
                                  --outputdir ${mergedDir} ^
                                  --nostatusrc ^
                                  ${currentOutput} ^
                                  ${RF_OUTPUT}/retry_${attempt}/output.xml
                        """
                        currentOutput = "${mergedDir}/output.xml"

                        // Check remaining env failures
                        try {
                            def countOutput = bat(
                                script: "python ${CLASSIFIER_SCRIPT} count-env-fail --output-xml ${currentOutput}",
                                returnStdout: true
                            ).trim()
                            envFailCount = countOutput.tokenize('\n').last().trim().toInteger()
                        } catch (Exception e) {
                            envFailCount = 0
                        }

                        echo "📊 After retry ${attempt}: ${envFailCount} env failures remaining"
                        if (envFailCount == 0) {
                            echo "✅ All environment failures resolved!"
                            break
                        }
                    }

                    env.FINAL_OUTPUT = currentOutput
                }
            }
        }

        // =================================================================
        // STAGE 4: REPORT
        // =================================================================
        stage('Report') {
            steps {
                script {
                    def outputXml = env.FINAL_OUTPUT ?: "${RF_OUTPUT}/execution/output.xml"

                    if (!fileExists(outputXml)) {
                        echo "⚠️ No output.xml available for reporting"
                        return
                    }

                    echo "📊 Generating report..."

                    try {
                        bat """
                            python ${CLASSIFIER_SCRIPT} report ^
                                  --output-xml ${outputXml} ^
                                  --report-dir ${RF_OUTPUT}/classified_report
                        """
                    } catch (Exception e) {
                        echo "⚠️ Report generation failed: ${e.message}"
                    }

                    try {
                        bat """
                            python ${CLASSIFIER_SCRIPT} summary ^
                                  --output-xml ${outputXml} ^
                                  --output-html ${RF_OUTPUT}/report_summary.html
                        """
                    } catch (Exception e) {
                        echo "⚠️ Summary generation failed: ${e.message}"
                    }
                }
            }
            post {
                always {
                    script {
                        def outputXml = env.FINAL_OUTPUT ?: "${RF_OUTPUT}/execution/output.xml"
                        def outputDir = outputXml.replace('/output.xml', '').replace('\\output.xml', '')

                        try {
                            step([
                                $class: 'RobotPublisher',
                                outputPath: outputDir,
                                outputFileName: 'output.xml',
                                reportFileName: 'report.html',
                                logFileName: 'log.html',
                                passThreshold: 80.0,
                                unstableThreshold: 60.0,
                                otherFiles: ''
                            ])
                        } catch (Exception e) {
                            echo "⚠️ Robot Publisher plugin not available: ${e.message}"
                        }
                    }

                    archiveArtifacts artifacts: "${RF_OUTPUT}/**", allowEmptyArchive: true
                }
            }
        }
    }

    // =====================================================================
    // POST-PIPELINE ACTIONS
    // =====================================================================
    post {
        always {
            echo """
            ╔══════════════════════════════════════════════════╗
            ║  HIL TEST EXECUTION COMPLETE                     ║
            ║  SW Version: ${params.SW_VERSION}                ║
            ║  Build:      #${BUILD_NUMBER}                    ║
            ║  Result:     ${currentBuild.result ?: 'SUCCESS'} ║
            ╚══════════════════════════════════════════════════╝
            """
        }
        success {
            script {
                if (params.EMAIL_RECIPIENTS?.trim()) {
                    emailext(
                        subject: "✅ [HIL] SW ${params.SW_VERSION} - Build #${BUILD_NUMBER} - SUCCESS",
                        body: '${FILE, path="' + "${RF_OUTPUT}/report_summary.html" + '"}',
                        to: params.EMAIL_RECIPIENTS,
                        mimeType: 'text/html'
                    )
                }
            }
        }
        failure {
            script {
                if (params.EMAIL_RECIPIENTS?.trim()) {
                    emailext(
                        subject: "❌ [HIL] SW ${params.SW_VERSION} - Build #${BUILD_NUMBER} - FAILED",
                        body: """
                            <h2>HIL Test Pipeline Failed</h2>
                            <p>SW Version: ${params.SW_VERSION}</p>
                            <p>Test Folder: ${params.TEST_FOLDER ?: 'Full Run'}</p>
                            <p>Build: <a href="${BUILD_URL}">#${BUILD_NUMBER}</a></p>
                            <p>Please check the build log for details.</p>
                        """,
                        to: params.EMAIL_RECIPIENTS,
                        mimeType: 'text/html'
                    )
                }
            }
        }
        unstable {
            script {
                if (params.EMAIL_RECIPIENTS?.trim()) {
                    emailext(
                        subject: "⚠️ [HIL] SW ${params.SW_VERSION} - Build #${BUILD_NUMBER} - UNSTABLE",
                        body: '${FILE, path="' + "${RF_OUTPUT}/report_summary.html" + '"}',
                        to: params.EMAIL_RECIPIENTS,
                        mimeType: 'text/html'
                    )
                }
            }
        }
    }
}
