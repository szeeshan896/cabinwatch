plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Every build used to be signed with whatever throwaway debug key the
// machine happened to have, and a fresh build machine makes a new one each
// time. Android will not install an app over one signed with a different
// key, so every new build arrived on the phone as "App not installed" -
// and the only way through was to uninstall first, losing the settings.
//
// With CABINWATCH_KEYSTORE set, builds are signed with one key that stays
// the same, and an update installs over the top like an update should. The
// keystore lives in the repository's secrets, never in the repository.
val keystorePath: String? = System.getenv("CABINWATCH_KEYSTORE")
val keystorePass: String = System.getenv("CABINWATCH_KEYSTORE_PASSWORD") ?: ""
val keyAliasName: String = System.getenv("CABINWATCH_KEY_ALIAS") ?: "cabinwatch"

android {
    namespace = "com.cabinwatch"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.cabinwatch"
        // Covers the older spare phones that tend to get used for this.
        minSdk = 24
        targetSdk = 34
        // Taken from the build number, so each build is a later version
        // than the one before it. A phone offered the same version number
        // twice has no reason to call it an update.
        versionCode = (System.getenv("CABINWATCH_BUILD") ?: "1").toInt()
        versionName = System.getenv("CABINWATCH_VERSION") ?: "1.0"
    }

    signingConfigs {
        if (keystorePath != null && file(keystorePath).exists()) {
            create("stable") {
                storeFile = file(keystorePath)
                storePassword = keystorePass
                keyAlias = keyAliasName
                keyPassword = keystorePass
            }
        }
    }

    buildTypes {
        // Both kinds are signed the same way when a key is available, so
        // whichever one somebody downloads can update the other.
        getByName("debug") {
            signingConfigs.findByName("stable")?.let { signingConfig = it }
        }
        release {
            signingConfigs.findByName("stable")?.let { signingConfig = it }
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
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
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.lifecycle:lifecycle-service:2.8.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // CameraX: capture, plus recording to the phone's own storage.
    implementation("androidx.camera:camera-core:1.3.4")
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-video:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")

    // Telling a person from a curtain, on the phone itself. The model is
    // bundled rather than downloaded, so a camera set up on an office wifi
    // and then carried somewhere else still works.
    implementation("com.google.mlkit:image-labeling:17.0.9")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
