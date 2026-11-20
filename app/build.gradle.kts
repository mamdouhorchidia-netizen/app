plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.vorath.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.vorath.mobile"
        minSdk = 26
        targetSdk = 35
        // CI passes its run number, so each published APK installs over the one before it.
        versionCode = System.getenv("VORATH_VERSION_CODE")?.toIntOrNull() ?: 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // The release key comes from the environment (CI decodes it from the repository's secrets); see docs/RELEASING.md.
    // Without it the release build is left unsigned; CI then makes a throwaway key and says so on the APK's name.
    val releaseKey = System.getenv("VORATH_KEYSTORE")?.takeIf { it.isNotBlank() && file(it).exists() }
    signingConfigs {
        if (releaseKey != null) {
            create("release") {
                storeFile = file(releaseKey)
                storePassword = System.getenv("VORATH_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("VORATH_KEY_ALIAS")
                keyPassword = System.getenv("VORATH_KEY_PASSWORD") ?: System.getenv("VORATH_KEYSTORE_PASSWORD")
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            // R8 and resource shrinking. The libraries bring their own keep rules (Compose, WorkManager's workers made
            // by class name, the ViewModel made by its constructor); proguard-rules.pro adds nothing beyond them.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (releaseKey != null) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
    sourceSets {
        // The emulator tests (src/androidTest) answer with the same recorded Vorath answers as the unit tests.
        getByName("androidTest").assets.srcDir("src/test/resources")
    }
    testOptions {
        unitTests.isIncludeAndroidResources = true
        unitTests.all {
            it.systemProperty("vorath.shots", System.getProperty("vorath.shots") ?: "")
            it.systemProperty("roborazzi.test.record", "true")
            // The live test (VorathLiveTest) runs only when a Vorath server and a sign-in are given.
            it.systemProperty("vorath.url", System.getProperty("vorath.url") ?: "")
            it.systemProperty("vorath.login", System.getProperty("vorath.login") ?: "")
        }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.05.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.0")
    implementation("androidx.work:work-runtime-ktx:2.10.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    // Pictures of the screens without a phone (ScreensTest): ./gradlew :app:testDebugUnitTest -Dvorath.shots=<folder>
    testImplementation("org.robolectric:robolectric:4.14.1")
    testImplementation("io.github.takahirom.roborazzi:roborazzi:1.43.1")
    testImplementation("io.github.takahirom.roborazzi:roborazzi-compose:1.43.1")
    testImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    // On an emulator (CI) or a phone: ./gradlew :app:connectedDebugAndroidTest
    androidTestImplementation(platform("androidx.compose:compose-bom:2025.05.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:rules:1.6.1")
    androidTestImplementation("androidx.work:work-testing:2.10.1")
}
