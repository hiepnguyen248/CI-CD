// =============================================================================
// HIL Test Automation - Master Pipeline
// Framework: Robot Framework | CI: Jenkins | Benches: 4-5 HIL
// =============================================================================
//
// Pipeline Architecture (5 Stages):
//   Stage 1: Env Validation      → 4-5 checks: SW stack, ECU ping, CAN, Power, Config
//   Stage 1.5: Test Selection    → Resolve FOLDER_LIST / TCID_LIST → argfile
//   Stage 2: Main Execution      → Parallel across all benches (~2500 TCs)
//                                   with HANG DETECTION + REALTIME TRACKING
//   Stage 3: Auto-Retry          → Classify failures + retry env-fails
//   Stage 4: Merge & Report      → Combine results + generate report
//
// Bench Allocation (2 variant support):
//   1 variant  → 5 benches parallel → ~5h
//   2 variants → chia đôi bench (ví dụ: bench 1,2 cho Variant A + bench 3,4,5 cho B)
//   Mỗi pipeline run nhận BENCH_ALLOCATION param → chạy chỉ trên subset benches
//
// eCall Isolation:
//   eCall = feature phức tạp nhất (nhiều step adhoc, multi-system, network instability)
//   → Luôn pin vào 1 bench cố định stable nhất
//   → Retry 3x thay vì 2x
//   → Hang timeout thoáng hơn (10 min thay vì 5 min)
//
// Hang Detection:
//   test_runner_wrapper.py monitor output - nếu không có output trong X giây
//   → kill process, skip test đang chạy, chạy tiếp các test còn lại.
// =============================================================================

pipeline {
    agent none

    parameters {
        choice(
            name: 'VARIANT',
            choices: ['Variant_A', 'Variant_B', 'Variant_C'],
            description: 'Select variant to test'
        )
        // ─── TEST SELECTION MODE ───
        choice(
            name: 'SELECTION_MODE',
            choices: ['auto', 'folder', 'tcid', 'full'],
            description: 'How to select tests: auto=detect from inputs, folder=FOLDER_LIST, tcid=TCID_LIST, full=run all'
        )
        choice(
            name: 'TEST_SCOPE',
            choices: ['full', 'smoke', 'regression', 'feature'],
            description: 'Test scope to execute'
        )
        string(
            name: 'FEATURE_FILTER',
            defaultValue: '',
            description: 'Comma-separated feature names (only for TEST_SCOPE=feature). E.g: HVAC,BCM,ADAS'
        )
        string(
            name: 'MAX_RETRY',
            defaultValue: '2',
            description: 'Max retry attempts for environment-failed test cases (eCall auto gets 3x)'
        )
        booleanParam(
            name: 'SKIP_ENV_CHECK',
            defaultValue: false,
            description: 'Skip environment validation (use when bench is known stable)'
        )
        string(
            name: 'HANG_TIMEOUT',
            defaultValue: '300',
            description: 'Seconds of no output before declaring a test as hung (default: 300 = 5min)'
        )
        string(
            name: 'TEST_TIMEOUT',
            defaultValue: '600',
            description: 'Max seconds per individual test case (default: 600 = 10min)'
        )
        // ─── BENCH ALLOCATION (cho 2 variants chạy đồng thời) ───
        string(
            name: 'BENCH_ALLOCATION',
            defaultValue: '',
            description: 'Comma-separated bench labels for THIS run. Leave empty = use all benches. E.g: hil-bench-1,hil-bench-2'
        )
        string(
            name: 'ECALL_BENCH',
            defaultValue: 'hil-bench-1',
            description: 'Dedicated bench for eCall (most stable bench). eCall always runs here.'
        )
        // ─── FOLDER SELECTION (chọn test theo folder path) ───
        text(
            name: 'FOLDER_LIST',
            defaultValue: '',
            description: 'Folder paths to run, relative to tests/ (1 per line or comma-separated). E.g: eCall,HVAC,BCM/SubFolder'
        )
        // ─── TCID SELECTION (khi được assign danh sách TCIDs) ───
        text(
            name: 'TCID_LIST',
            defaultValue: '',
            description: 'Danh sách TCID cần chạy (1 TCID/dòng hoặc comma-separated). Để trống = chạy full. Ví dụ: TC001,TC002,TC045'
        )
    }

    environment {
        RF_OUTPUT         = "results/${params.VARIANT}/${BUILD_NUMBER}"
        ROBOT_OPTS        = "--variable VARIANT:${params.VARIANT} --loglevel DEBUG"
        CLASSIFIER_SCRIPT = "cicd/scripts/failure_classifier.py"
        WRAPPER_SCRIPT    = "cicd/scripts/test_runner_wrapper.py"
        TCID_MAPPER       = "cicd/scripts/tcid_mapper.py"
        TRACKER_SCRIPT    = "cicd/scripts/realtime_tracker.py"
        ENV_CHECK_SUITE   = "cicd/env_check/env_validation.robot"
        // Adjust paths to your project structure
        TEST_DIR          = "tests"
    }

    options {
        timestamps()
        timeout(time: 24, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '30'))
        disableConcurrentBuilds(abortPrevious: false)
    }

    triggers {
        // Nightly run: 8PM Mon-Fri
        // Khi chạy 2 variants: setup 2 Jenkins jobs cùng Jenkinsfile, khác VARIANT + BENCH_ALLOCATION
        //   Job "Variant_A_Nightly" → BENCH_ALLOCATION=hil-bench-1,hil-bench-2
        //   Job "Variant_B_Nightly" → BENCH_ALLOCATION=hil-bench-3,hil-bench-4,hil-bench-5
        cron('H 20 * * 1-5')
    }

    stages {
        // =====================================================================
        // STAGE 1: ENVIRONMENT VALIDATION (thay Smoke Test)
        // =====================================================================
        // Chạy 4-5 test cases kiểm tra bench trước khi chạy 2500 TCs:
        //   1. Software stack (Python, Robot, libraries)
        //   2. ECU network connectivity (ping)
        //   3. CAN/Diagnostic interface (CANoe, Vector HW)
        //   4. Power supply & relay board
        //   5. Test config files (DBC, ARXML...)
        // Nếu FAIL → pipeline abort ngay, tiết kiệm thời gian.
        // =====================================================================
        stage('Environment Validation') {
            when {
                expression { return !params.SKIP_ENV_CHECK }
            }
            steps {
                script {
                    echo "🔍 Running Environment Validation on ALL benches..."
                    def benches = getBenchLabels()
                    def envCheckBranches = [:]
                    def benchResults = [:]

                    // Run env check on each bench in parallel
                    benches.each { benchLabel ->
                        def currentBench = benchLabel
                        envCheckBranches[currentBench] = {
                            node(currentBench) {
                                checkout scm
                                try {
                                    bat """
                                        robot --outputdir ${RF_OUTPUT}/env_check/${currentBench} ^
                                              --loglevel DEBUG ^
                                              ${ENV_CHECK_SUITE}
                                    """
                                    benchResults[currentBench] = 'PASS'
                                } catch (Exception e) {
                                    benchResults[currentBench] = 'FAIL'
                                    echo "❌ [${currentBench}] Environment check FAILED!"
                                }
                            }
                        }
                    }

                    parallel envCheckBranches

                    // Evaluate results
                    echo "\n📊 Environment Validation Results:"
                    def failedBenches = []
                    def passedBenches = []
                    benchResults.each { bench, status ->
                        def icon = status == 'PASS' ? '✅' : '❌'
                        echo "  ${icon} ${bench}: ${status}"
                        if (status == 'FAIL') {
                            failedBenches.add(bench)
                        } else {
                            passedBenches.add(bench)
                        }
                    }

                    if (passedBenches.isEmpty()) {
                        error("""
                            ❌ ALL benches failed environment validation!
                            ┌──────────────────────────────────────────┐
                            │  NO BENCH IS READY FOR TESTING           │
                            │  Pipeline aborted to save execution time │
                            │  Check: ECU power, CAN cable, licenses   │
                            └──────────────────────────────────────────┘
                        """)
                    }

                    if (!failedBenches.isEmpty()) {
                        echo "⚠️ ${failedBenches.size()} bench(es) failed. Continuing with: ${passedBenches}"
                        // Store healthy benches for Main Execution
                        env.HEALTHY_BENCHES = passedBenches.join(',')
                        unstable("Some benches failed env check: ${failedBenches}")
                    } else {
                        env.HEALTHY_BENCHES = passedBenches.join(',')
                        echo "✅ All benches passed environment validation!"
                    }
                }
            }
        }

        // =====================================================================
        // STAGE 1.5: RESOLVE TEST SELECTION
        // =====================================================================
        // Unified selection stage: handles FOLDER_LIST, TCID_LIST, or full run.
        // Priority: SELECTION_MODE → TCID_LIST → FOLDER_LIST → full
        //   folder mode: validate folders, scan .robot files, generate argfile
        //   tcid mode:   resolve TCIDs → .robot files → argfile
        //   full mode:   skip, run everything
        // =====================================================================
        stage('Resolve Test Selection') {
            when {
                expression {
                    def mode = params.SELECTION_MODE ?: 'auto'
                    if (mode == 'full') return false
                    if (mode == 'folder') return true
                    if (mode == 'tcid') return true
                    // auto: detect from inputs
                    return params.TCID_LIST?.trim() || params.FOLDER_LIST?.trim()
                }
            }
            agent { label getBenchLabels()[0] }
            steps {
                checkout scm
                script {
                    def mode = params.SELECTION_MODE ?: 'auto'
                    // Auto-detect mode from inputs
                    if (mode == 'auto') {
                        if (params.TCID_LIST?.trim()) {
                            mode = 'tcid'
                        } else if (params.FOLDER_LIST?.trim()) {
                            mode = 'folder'
                        }
                    }

                    // ─── FOLDER MODE ───
                    if (mode == 'folder') {
                        echo "📁 FOLDER MODE: Selecting tests by folder path"
                        def folderList = params.FOLDER_LIST.trim()
                        def folderFile = "${RF_OUTPUT}/folder_list.txt"
                        writeFile file: folderFile, text: folderList

                        def folderCount = folderList.split(/[,\n]/).findAll{it.trim()}.size()
                        echo "🔍 Resolving ${folderCount} folder(s)..."

                        // Resolve folders → argfile
                        bat """
                            python ${TCID_MAPPER} resolve-folders ^
                                --folder-list "${folderList.replaceAll('\n', ',')}" ^
                                --test-dir ${TEST_DIR} ^
                                --output ${RF_OUTPUT}/selection_argfile.txt ^
                                --format json
                        """

                        // Get feature grouping for bench distribution
                        def groupJson = bat(
                            script: "python ${TCID_MAPPER} resolve-folders --folder-list \"${folderList.replaceAll('\n', ',')}\" --test-dir ${TEST_DIR} --format json",
                            returnStdout: true
                        ).trim()

                        // Extract feature list from JSON
                        def featureData = readJSON(text: groupJson)
                        def featureList = featureData.features?.keySet()?.toList() ?: []

                        env.SELECTION_MODE = 'folder'
                        env.SELECTION_ARGFILE = "${RF_OUTPUT}/selection_argfile.txt"
                        env.SELECTION_FEATURES = writeJSON(returnText: true, json: featureData.features ?: [:])
                        echo "✅ Folder selection complete: ${featureList} (${featureData.stats?.robot_files ?: '?'} .robot files)"

                    // ─── TCID MODE ───
                    } else if (mode == 'tcid') {
                        echo "🔢 TCID MODE: Selecting tests by TCID list"
                        def tcidFile = "${RF_OUTPUT}/tcid_list.txt"
                        writeFile file: tcidFile, text: params.TCID_LIST

                        echo "🔍 Resolving ${params.TCID_LIST.split(/[,\n]/).findAll{it.trim()}.size()} TCIDs..."
                        bat """
                            python ${TCID_MAPPER} resolve ^
                                --tcid-file ${tcidFile} ^
                                --test-dir ${TEST_DIR}
                        """

                        bat """
                            python ${TCID_MAPPER} generate-argfile ^
                                --tcid-file ${tcidFile} ^
                                --test-dir ${TEST_DIR} ^
                                --output ${RF_OUTPUT}/selection_argfile.txt
                        """

                        def groupJson = bat(
                            script: "python ${TCID_MAPPER} group --tcid-file ${tcidFile} --test-dir ${TEST_DIR} --format json",
                            returnStdout: true
                        ).trim()

                        env.SELECTION_MODE = 'tcid'
                        env.SELECTION_ARGFILE = "${RF_OUTPUT}/selection_argfile.txt"
                        env.SELECTION_FEATURES = groupJson
                        echo "✅ TCID resolution complete. Argfile: ${env.SELECTION_ARGFILE}"
                    }
                }
            }
        }

        // =====================================================================
        // STAGE 2: MAIN EXECUTION - PARALLEL ACROSS ALL BENCHES
        // =====================================================================
        // 3 modes:
        //   A) Folder mode: run only selected folders (from FOLDER_LIST)
        //   B) TCID mode:   run only assigned TCs (from TCID_LIST)
        //   C) Full mode:   run all features
        //
        // Realtime Tracking:
        //   Each bench writes tracker_{bench}.json → Jenkins polls + prints summary
        //   tracker_file passed via --tracker-file to test_runner_wrapper.py
        // =====================================================================
        stage('Main Execution') {
            steps {
                script {
                    echo "🏗️ Starting main execution across available benches..."
                    echo "🛡️ Hang detection ENABLED (timeout: ${params.HANG_TIMEOUT}s)"
                    echo "📊 Realtime tracking ENABLED"

                    // Determine which benches to use
                    def healthyBenches = env.HEALTHY_BENCHES
                        ? env.HEALTHY_BENCHES.split(',').toList()
                        : getBenchLabels()

                    if (params.BENCH_ALLOCATION?.trim()) {
                        def allocated = params.BENCH_ALLOCATION.split(',').collect { it.trim() }
                        healthyBenches = healthyBenches.intersect(allocated)
                        echo "📌 Bench allocation: ${allocated} → healthy subset: ${healthyBenches}"
                        if (healthyBenches.isEmpty()) {
                            error("❌ No healthy benches in allocation: ${allocated}")
                        }
                    }

                    // Determine selection mode
                    def selectionMode = env.SELECTION_MODE ?: 'full'
                    def selectionArgfile = env.SELECTION_ARGFILE ?: ''
                    def selectionFeatures = env.SELECTION_FEATURES ?: ''

                    // ─── MODE A: Selection Mode (folder or TCID) ───
                    if (selectionMode in ['folder', 'tcid'] && selectionFeatures) {
                        echo "📋 ${selectionMode.toUpperCase()} MODE: Running selected test cases"
                        def featuresData = readJSON(text: selectionFeatures)
                        def featureList = featuresData.keySet().findAll { !it.startsWith('_') }.toList()

                        def distribution = getFeatureDistribution(healthyBenches, featureList)
                        def parallelBranches = [:]

                        distribution.each { benchLabel, featureConfig ->
                            def currentBench = benchLabel
                            def currentFeatures = featureConfig.features

                            parallelBranches["${currentBench}"] = {
                                node(currentBench) {
                                    checkout scm

                                    for (feature in currentFeatures) {
                                        def hangTimeout = (feature == 'eCall') ? '600' : params.HANG_TIMEOUT
                                        def testTimeout = (feature == 'eCall') ? '900' : params.TEST_TIMEOUT
                                        def trackerFile = "${RF_OUTPUT}/execution/${currentBench}/tracker_${currentBench}.json"

                                        // Build test filters for TCID mode
                                        def testFilters = ''
                                        if (selectionMode == 'tcid') {
                                            def featureTcids = featuresData[feature]
                                            testFilters = featureTcids?.collect { "--test *${it.tcid}*" }?.join(' ') ?: ''
                                        }

                                        echo "▶️ [${currentBench}] Feature: ${feature} (hang: ${hangTimeout}s)"
                                        try {
                                            bat """
                                                python ${WRAPPER_SCRIPT} ^
                                                    --suite ${TEST_DIR}/${feature} ^
                                                    --outputdir ${RF_OUTPUT}/execution/${currentBench}/${feature} ^
                                                    --hang-timeout ${hangTimeout} ^
                                                    --test-timeout ${testTimeout} ^
                                                    --tracker-file ${trackerFile} ^
                                                    --bench-name ${currentBench} ^
                                                    --feature-name ${feature} ^
                                                    -- ${testFilters} ^
                                                       ${ROBOT_OPTS}
                                            """
                                        } catch (Exception e) {
                                            echo "⚠️ [${currentBench}] Feature ${feature} completed with failures/hangs"
                                        }
                                    }
                                }
                            }
                        }

                        // ─── Realtime Tracker Poller (parallel with execution) ───
                        parallelBranches['📊 Realtime Tracker'] = {
                            node(healthyBenches[0]) {
                                for (int poll = 0; poll < 720; poll++) {  // max 6h
                                    sleep(30)
                                    try {
                                        bat """
                                            python ${TRACKER_SCRIPT} summary ^
                                                --dir ${RF_OUTPUT}/execution
                                        """
                                    } catch (Exception e) {
                                        // Normal during startup before tracker files exist
                                    }
                                }
                            }
                        }
                        parallel parallelBranches

                    // ─── MODE B: Full Execution Mode ───
                    } else {
                        echo "📋 FULL MODE: Running all test cases"
                        def distribution = getFeatureDistribution(healthyBenches)
                        def parallelBranches = [:]

                        distribution.each { benchLabel, featureConfig ->
                            def currentBench = benchLabel
                            def currentFeatures = featureConfig.features
                            def currentEnvFilter = featureConfig.envFilter

                            parallelBranches["${currentBench}"] = {
                                node(currentBench) {
                                    checkout scm

                                    for (feature in currentFeatures) {
                                        def hangTimeout = (feature == 'eCall') ? '600' : params.HANG_TIMEOUT
                                        def testTimeout = (feature == 'eCall') ? '900' : params.TEST_TIMEOUT
                                        def trackerFile = "${RF_OUTPUT}/execution/${currentBench}/tracker_${currentBench}.json"

                                        echo "▶️ [${currentBench}] Feature: ${feature} (hang: ${hangTimeout}s, test: ${testTimeout}s)"
                                        try {
                                            bat """
                                                python ${WRAPPER_SCRIPT} ^
                                                    --suite ${TEST_DIR}/${feature} ^
                                                    --outputdir ${RF_OUTPUT}/execution/${currentBench}/${feature} ^
                                                    --hang-timeout ${hangTimeout} ^
                                                    --test-timeout ${testTimeout} ^
                                                    --tracker-file ${trackerFile} ^
                                                    --bench-name ${currentBench} ^
                                                    --feature-name ${feature} ^
                                                    -- ${currentEnvFilter} ^
                                                       ${ROBOT_OPTS}
                                            """
                                        } catch (Exception e) {
                                            echo "⚠️ [${currentBench}] Feature ${feature} completed with failures/hangs"
                                        }
                                    }
                                }
                            }
                        }

                        // ─── Realtime Tracker Poller (parallel with execution) ───
                        parallelBranches['📊 Realtime Tracker'] = {
                            node(healthyBenches[0]) {
                                for (int poll = 0; poll < 720; poll++) {  // max 6h
                                    sleep(30)
                                    try {
                                        bat """
                                            python ${TRACKER_SCRIPT} summary ^
                                                --dir ${RF_OUTPUT}/execution
                                        """
                                    } catch (Exception e) {
                                        // Normal during startup
                                    }
                                }
                            }
                        }
                        parallel parallelBranches
                    }

                    // ─── Final aggregate tracker status ───
                    echo "📊 Generating final tracker aggregate..."
                    try {
                        bat """
                            python ${TRACKER_SCRIPT} aggregate ^
                                --dir ${RF_OUTPUT}/execution ^
                                --output ${RF_OUTPUT}/tracker_final.json
                        """
                    } catch (Exception e) {
                        echo "⚠️ Tracker aggregation failed: ${e.message}"
                    }
                }
            }
        }

        // =====================================================================
        // STAGE 3: AUTO-RETRY ENVIRONMENT FAILURES
        // =====================================================================
        stage('Auto-Retry Env Failures') {
            agent { label 'hil-bench-1' }
            steps {
                script {
                    echo "🔄 Classifying failures and retrying environment issues..."

                    // Step 1: First merge all execution results
                    bat """
                        rebot --merge ^
                              --outputdir ${RF_OUTPUT}/merged ^
                              --nostatusrc ^
                              ${RF_OUTPUT}/execution/**/output.xml
                    """

                    // Step 2: Classify failures
                    def envFailCount = bat(
                        script: "python ${CLASSIFIER_SCRIPT} count-env-fail --output-xml ${RF_OUTPUT}/merged/output.xml",
                        returnStdout: true
                    ).trim().tokenize('\n').last().trim().toInteger()

                    echo "📊 Environment failures found: ${envFailCount}"

                    if (envFailCount == 0) {
                        echo "✅ No environment failures to retry."
                        return
                    }

                    // Step 3: Retry loop
                    def maxRetry = params.MAX_RETRY.toInteger()
                    for (int attempt = 1; attempt <= maxRetry; attempt++) {
                        echo "🔄 Retry attempt ${attempt}/${maxRetry} (${envFailCount} env failures remaining)"

                        try {
                            bat """
                                robot --rerunfailed ${RF_OUTPUT}/merged/output.xml ^
                                      --outputdir ${RF_OUTPUT}/retry_${attempt} ^
                                      ${ROBOT_OPTS} ^
                                      ${TEST_DIR}
                            """
                        } catch (Exception e) {
                            echo "⚠️ Retry ${attempt} completed with some failures"
                        }

                        // Merge retry results back
                        bat """
                            rebot --merge ^
                                  --outputdir ${RF_OUTPUT}/merged ^
                                  --nostatusrc ^
                                  ${RF_OUTPUT}/merged/output.xml ^
                                  ${RF_OUTPUT}/retry_${attempt}/output.xml
                        """

                        // Check remaining env failures
                        envFailCount = bat(
                            script: "python ${CLASSIFIER_SCRIPT} count-env-fail --output-xml ${RF_OUTPUT}/merged/output.xml",
                            returnStdout: true
                        ).trim().tokenize('\n').last().trim().toInteger()

                        echo "📊 After retry ${attempt}: ${envFailCount} env failures remaining"

                        if (envFailCount == 0) {
                            echo "✅ All environment failures resolved after ${attempt} retry(s)!"
                            break
                        }
                    }
                }
            }
        }

        // =====================================================================
        // STAGE 4: MERGE RESULTS & GENERATE REPORT
        // =====================================================================
        stage('Report & Classify') {
            agent { label 'hil-bench-1' }
            steps {
                script {
                    echo "📊 Generating final report..."

                    // Generate classified failure report
                    bat """
                        python ${CLASSIFIER_SCRIPT} report ^
                              --output-xml ${RF_OUTPUT}/merged/output.xml ^
                              --report-dir ${RF_OUTPUT}/classified_report
                    """

                    // Generate summary for email
                    bat """
                        python ${CLASSIFIER_SCRIPT} summary ^
                              --output-xml ${RF_OUTPUT}/merged/output.xml ^
                              --output-html ${RF_OUTPUT}/report_summary.html
                    """
                }
            }
            post {
                always {
                    // Publish Robot Framework results in Jenkins UI
                    script {
                        try {
                            step([
                                $class: 'RobotPublisher',
                                outputPath: "${RF_OUTPUT}/merged",
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

                    // Archive all artifacts
                    archiveArtifacts artifacts: "${RF_OUTPUT}/**", allowEmptyArchive: true
                }
            }
        }
    }

    // =========================================================================
    // POST-PIPELINE ACTIONS
    // =========================================================================
    post {
        always {
            script {
                echo """
                ╔══════════════════════════════════════════════════╗
                ║  HIL TEST EXECUTION COMPLETE                    ║
                ║  Variant: ${params.VARIANT}                     ║
                ║  Build:   #${BUILD_NUMBER}                      ║
                ║  Result:  ${currentBuild.result ?: 'SUCCESS'}   ║
                ╚══════════════════════════════════════════════════╝
                """
            }
        }
        success {
            emailext(
                subject: "✅ [HIL] ${params.VARIANT} - Build #${BUILD_NUMBER} - SUCCESS",
                body: '${FILE, path="${RF_OUTPUT}/report_summary.html"}',
                to: 'team@company.com',
                mimeType: 'text/html'
            )
        }
        failure {
            emailext(
                subject: "❌ [HIL] ${params.VARIANT} - Build #${BUILD_NUMBER} - FAILED",
                body: '''
                    <h2>HIL Test Pipeline Failed</h2>
                    <p>Variant: ${VARIANT}</p>
                    <p>Build: <a href="${BUILD_URL}">#${BUILD_NUMBER}</a></p>
                    <p>Please check the build log for details.</p>
                ''',
                to: 'team@company.com',
                mimeType: 'text/html'
            )
        }
        unstable {
            emailext(
                subject: "⚠️ [HIL] ${params.VARIANT} - Build #${BUILD_NUMBER} - UNSTABLE",
                body: '${FILE, path="${RF_OUTPUT}/report_summary.html"}',
                to: 'team@company.com',
                mimeType: 'text/html'
            )
        }
    }
}

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

/**
 * Get list of bench labels available in Jenkins.
 * Customize this based on your actual Jenkins agent labels.
 */
def getBenchLabels() {
    return ['hil-bench-1', 'hil-bench-2', 'hil-bench-3', 'hil-bench-4', 'hil-bench-5']
}

/**
 * Auto-discover features from folder structure.
 * Mỗi folder con trực tiếp trong TEST_DIR chứa ít nhất 1 .robot file = 1 feature.
 * 
 * tests/
 * ├── eCall/  (chứa .robot)  → feature "eCall"
 * ├── HVAC/   (chứa .robot)  → feature "HVAC"
 * ├── BCM/    (chứa .robot)  → feature "BCM"
 * └── data/   (không có .robot) → bỏ qua
 */
def discoverFeatures() {
    def features = []
    def testDir = new File("${env.WORKSPACE}/${TEST_DIR}")
    
    if (testDir.exists() && testDir.isDirectory()) {
        testDir.listFiles().each { subDir ->
            if (subDir.isDirectory()) {
                // Check if folder contains any .robot files (recursive)
                def hasRobot = subDir.listFiles()?.any { 
                    it.name.endsWith('.robot') 
                } ?: false
                if (!hasRobot) {
                    // Also check subdirectories
                    hasRobot = subDir.listFiles()?.any { sub ->
                        sub.isDirectory() && sub.listFiles()?.any { it.name.endsWith('.robot') }
                    } ?: false
                }
                if (hasRobot) {
                    features.add(subDir.name)
                }
            }
        }
    }
    
    if (features.isEmpty()) {
        echo "⚠️ No feature folders found in ${TEST_DIR}. Using fallback list."
        features = ['eCall', 'HVAC', 'BCM', 'ADAS', 'Cluster',
                     'Audio', 'Display', 'Network', 'Diag',
                     'Power', 'Safety', 'Infotainment', 'Body']
    }
    
    echo "📂 Discovered features: ${features}"
    return features
}

/**
 * Get feature distribution across benches.
 * 
 * Design decisions:
 *   1. eCall LUÔN pin vào ECALL_BENCH (bench stable nhất) vì TC phức tạp,
 *      nhiều step adhoc, check nhiều hệ thống, mạng không ổn định
 *   2. Các features còn lại round-robin across normal benches
 *   3. Special env TCs → bench cuối cùng
 *   4. Với 2 variants: BENCH_ALLOCATION quyết định bench nào cho run này
 *
 * Ví dụ phân chia 2 variants:
 *   Job "Variant_A" → BENCH_ALLOCATION=hil-bench-1,hil-bench-2
 *     → bench-1: eCall (dedicated) + HVAC, BCM   (~600 TCs)
 *     → bench-2: ADAS, Cluster, Audio, ...        (~600 TCs + special env)
 *   Job "Variant_B" → BENCH_ALLOCATION=hil-bench-3,hil-bench-4,hil-bench-5
 *     → bench-3: eCall (dedicated) + HVAC, BCM   (~500 TCs)
 *     → bench-4: ADAS, Cluster, Audio             (~500 TCs)
 *     → bench-5: Network, Diag, ... + special env (~500 TCs)
 *
 * @param availableBenches - list of healthy bench labels (from env validation)
 */
def getFeatureDistribution(List availableBenches = null, List featureOverride = null) {
    // ─── Feature list ───
    // Ưu tiên: featureOverride (từ TCID resolve) > FEATURE_FILTER > hardcoded list
    def allFeatures
    if (featureOverride) {
        allFeatures = featureOverride
    } else if (params.TEST_SCOPE == 'feature' && params.FEATURE_FILTER?.trim()) {
        allFeatures = params.FEATURE_FILTER.split(',').collect { it.trim() }
    } else {
        // Auto-detect features from folder structure
        // Mỗi folder con trực tiếp trong tests/ = 1 feature
        allFeatures = discoverFeatures()
    }

    def benches = availableBenches ?: getBenchLabels()
    def distribution = [:]

    // ─── STEP 1: Pin eCall to dedicated bench ───
    def ecallBench = params.ECALL_BENCH ?: benches[0]
    // If eCall bench is not in our allocation, use first bench
    if (!benches.contains(ecallBench)) {
        ecallBench = benches[0]
    }

    def hasEcall = allFeatures.contains('eCall')
    def remainingFeatures = allFeatures.findAll { it != 'eCall' }

    if (hasEcall) {
        distribution[ecallBench] = [
            features: ['eCall'],
            envFilter: '--exclude env:special_*'
        ]
        echo "📌 eCall pinned to ${ecallBench} (dedicated, hang timeout 10min, retry 3x)"
    }

    // ─── STEP 2: Distribute remaining features across other benches ───
    def otherBenches = benches.findAll { it != ecallBench }
    if (otherBenches.isEmpty()) {
        // Only 1 bench available → eCall bench gets everything
        distribution[ecallBench].features.addAll(remainingFeatures)
    } else {
        // Keep last bench for special env, use rest for normal
        def normalBenches = otherBenches.size() > 1 ? otherBenches[0..-2] : otherBenches
        def specialBench = otherBenches[-1]

        // Round-robin remaining features across normal benches
        remainingFeatures.eachWithIndex { feature, idx ->
            def targetBench = normalBenches[idx % normalBenches.size()]
            if (!distribution.containsKey(targetBench)) {
                distribution[targetBench] = [features: [], envFilter: '--exclude env:special_*']
            }
            distribution[targetBench].features.add(feature)
        }

        // eCall bench also gets some normal features if it has capacity
        // (eCall alone is ~200 TCs, bench can handle more)
        if (normalBenches.size() > 0 && remainingFeatures.size() > normalBenches.size() * 3) {
            // Redistribute overflow features to eCall bench
            def overflow = remainingFeatures.size() - (normalBenches.size() * 3)
            def overflowFeatures = remainingFeatures[-overflow..-1]
            distribution[ecallBench].features.addAll(overflowFeatures)
        }

        // Special bench: run ONLY env:special_* tagged TCs across ALL features
        distribution[specialBench] = [
            features: allFeatures,
            envFilter: '--include env:special_*'
        ]
    }

    // ─── Log distribution plan ───
    echo "\n📋 Feature Distribution Plan (${benches.size()} benches):"
    echo "─────────────────────────────────────────────"
    distribution.each { bench, config ->
        def ecallTag = config.features.contains('eCall') ? ' ⭐eCall' : ''
        echo "  ${bench}: ${config.features}${ecallTag}"
        echo "    Filter: ${config.envFilter}"
    }
    echo "─────────────────────────────────────────────"

    return distribution
}

/**
 * Get max retry count for a feature.
 * eCall gets 3x (complex, multi-system, network flaky)
 * Other features get default MAX_RETRY (usually 2x)
 */
def getMaxRetry(String feature) {
    if (feature == 'eCall') {
        return 3
    }
    return params.MAX_RETRY?.toInteger() ?: 2
}

/**
 * Calculate pass rate from Robot Framework output.xml.
 */
def calculatePassRate(String outputXml) {
    def result = bat(
        script: "python -c \"from robot.api import ExecutionResult; r = ExecutionResult('${outputXml}'); stats = r.statistics.total; print(int(stats.passed / max(stats.total, 1) * 100))\"",
        returnStdout: true
    ).trim().tokenize('\n').last().trim().toInteger()
    return result
}
