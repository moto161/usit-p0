package org.usit.instrumentation

import android.util.Log
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * Minimal test-only event emitter for USIT Android JSONL ingestion.
 *
 * Place this file in an androidTest source set, not production app code.
 * Never pass credentials, headers, request/response bodies, personal data,
 * raw database values, or arbitrary log messages to this emitter.
 */
object UsitEventEmitter {
    private const val TAG = "USIT"
    private const val PREFIX = "USIT_ANDROID_EVENT "

    @Synchronized
    private fun emit(
        event: String,
        correlationId: String,
        parentEventId: String? = null,
        businessFunctionId: String? = null,
        fields: Map<String, Any?> = emptyMap()
    ): String {
        require(correlationId.isNotBlank()) { "correlationId is required" }
        val eventId = UUID.randomUUID().toString()
        val formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US).apply {
            timeZone = TimeZone.getDefault()
        }
        val json = JSONObject()
            .put("event_id", eventId)
            .put("timestamp", formatter.format(Date()))
            .put("event", event)
            .put("correlation_id", correlationId)

        if (parentEventId != null) json.put("parent_event_id", parentEventId)
        if (businessFunctionId != null) json.put("business_function_id", businessFunctionId)

        val allowed = setOf(
            "screen_id", "control_id", "action", "result", "duration_ms",
            "http_method", "route_template", "status_code", "db_operation",
            "table_or_collection", "permission", "granted", "app_state", "actor_ref"
        )
        fields.forEach { (key, value) ->
            require(key in allowed) { "unsupported USIT event field: $key" }
            if (value != null) json.put(key, value)
        }
        Log.i(TAG, PREFIX + json.toString())
        return eventId
    }

    fun screenOpened(
        correlationId: String,
        screenId: String,
        businessFunctionId: String? = null,
        parentEventId: String? = null
    ): String = emit(
        event = "screen_opened",
        correlationId = correlationId,
        parentEventId = parentEventId,
        businessFunctionId = businessFunctionId,
        fields = mapOf("screen_id" to screenId, "app_state" to "RESUMED")
    )

    fun uiAction(
        correlationId: String,
        screenId: String,
        controlId: String,
        action: String,
        result: String,
        businessFunctionId: String? = null,
        parentEventId: String? = null
    ): String = emit(
        event = "ui_action",
        correlationId = correlationId,
        parentEventId = parentEventId,
        businessFunctionId = businessFunctionId,
        fields = mapOf(
            "screen_id" to screenId,
            "control_id" to controlId,
            "action" to action,
            "result" to result
        )
    )

    fun permissionResult(
        correlationId: String,
        permission: String,
        granted: Boolean,
        parentEventId: String? = null
    ): String = emit(
        event = "permission_result",
        correlationId = correlationId,
        parentEventId = parentEventId,
        fields = mapOf("permission" to permission, "granted" to granted)
    )

    fun httpRequest(
        correlationId: String,
        method: String,
        routeTemplate: String,
        parentEventId: String? = null,
        businessFunctionId: String? = null
    ): String = emit(
        event = "http_request",
        correlationId = correlationId,
        parentEventId = parentEventId,
        businessFunctionId = businessFunctionId,
        fields = mapOf("http_method" to method, "route_template" to routeTemplate)
    )

    fun httpResponse(
        correlationId: String,
        statusCode: Int,
        parentEventId: String
    ): String = emit(
        event = "http_response",
        correlationId = correlationId,
        parentEventId = parentEventId,
        fields = mapOf("status_code" to statusCode)
    )
}
