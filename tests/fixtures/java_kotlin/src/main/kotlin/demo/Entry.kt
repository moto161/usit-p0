package demo
import demo.same as alias
import kotlinx.coroutines.delay
fun entry() {
    alias("x")
    same("y")
    dynamic()
    delay(1)
}
