package com.websarva.wings.android.sensordataapp

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.ext.junit.runners.AndroidJUnit4

import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

import org.junit.Assert.*

/**
 * Instrumented test, which will execute on an Android device.
 *
 * See [testing documentation](http://d.android.com/tools/testing).
 */
@RunWith(AndroidJUnit4::class)
class ExampleInstrumentedTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun useAppContext() {
        // Context of the app under test.
        val appContext = InstrumentationRegistry.getInstrumentation().targetContext
        assertEquals("com.websarva.wings.android.sensordataapp", appContext.packageName)
    }

    @Test
    fun periodAverageResponseUsesServerDisplayValues() {
        val result = parsePeriodAverageResponse(
            """
            {
              "status": "success",
              "averages": {
                "start_at": "2026-07-14 09:00:00",
                "end_at": "2026-07-14 10:00:59",
                "temperature": {"value": 25.0, "display": "25.00℃", "count": 2},
                "humidity": {"value": 50.0, "display": "50.00%", "count": 2}
              }
            }
            """.trimIndent()
        )

        assertEquals("25.00℃", result.temperature.display)
        assertEquals(2, result.temperature.count)
        assertEquals("50.00%", result.humidity.display)
        assertEquals(2, result.humidity.count)
    }

    @Test
    fun periodAverageRequestMatchesUnchangedSelection() {
        assertTrue(
            periodAverageRequestMatchesSelection(
                "2026-07-14T09:00",
                "2026-07-14T10:00",
                "2026-07-14T09:00",
                "2026-07-14T10:00"
            )
        )
    }

    @Test
    fun periodAverageRequestRejectsChangedSelection() {
        assertFalse(
            periodAverageRequestMatchesSelection(
                "2026-07-14T09:00",
                "2026-07-14T10:00",
                "2026-07-14T11:00",
                "2026-07-14T10:00"
            )
        )
        assertFalse(
            periodAverageRequestMatchesSelection(
                "2026-07-14T09:00",
                "2026-07-14T10:00",
                "2026-07-14T09:00",
                "2026-07-14T12:00"
            )
        )
    }

    @Test
    fun periodDateButtonsAreDisabledWhileLoading() {
        composeTestRule.setContent {
            MaterialTheme {
                PeriodAverageDateButtons(
                    startLabel = "開始日時",
                    endLabel = "終了日時",
                    enabled = false,
                    onStartClick = {},
                    onEndClick = {}
                )
            }
        }

        composeTestRule.onNodeWithText("開始日時").assertIsNotEnabled()
        composeTestRule.onNodeWithText("終了日時").assertIsNotEnabled()
    }
}
