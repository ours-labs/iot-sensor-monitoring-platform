package com.websarva.wings.android.sensordataapp

import org.junit.Test

import org.junit.Assert.*

/**
 * Example local unit test, which will execute on the development machine (host).
 *
 * See [testing documentation](http://d.android.com/tools/testing).
 */
class ExampleUnitTest {
    @Test
    fun uniqueDeviceNameDoesNotShowId() {
        val device = DeviceChoice("12345678-aaaa", "教室Pi")
        assertEquals("教室Pi", deviceChoiceLabel(device, listOf(device)))
    }

    @Test
    fun duplicateDeviceNameShowsShortId() {
        val first = DeviceChoice("12345678-aaaa", "教室Pi")
        val second = DeviceChoice("87654321-bbbb", "教室Pi")
        val devices = listOf(first, second)
        assertEquals("教室Pi（12345678…）", deviceChoiceLabel(first, devices))
        assertEquals("教室Pi（87654321…）", deviceChoiceLabel(second, devices))
    }

    @Test
    fun htmlApiResponseHasStableAuthenticationError() {
        val error = assertThrows(IllegalStateException::class.java) {
            parseApiJsonObject("<!DOCTYPE html><html></html>")
        }
        assertTrue(error.message.orEmpty().contains("AUTH-A001"))
    }

    @Test
    fun jsonApiResponseIsAccepted() {
        assertTrue(isJsonObjectBody("{\"status\":\"success\"}"))
        assertFalse(isJsonObjectBody("<!DOCTYPE html>"))
    }

    @Test
    fun addition_isCorrect() {
        assertEquals(4, 2 + 2)
    }
}