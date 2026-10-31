package demo;
import static demo.A.same;
import third.party.External;
public class Main {
    public String run() {
        same("x");
        return External.call();
    }
}
