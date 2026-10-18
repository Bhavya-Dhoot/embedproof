/** Three slowly drifting colour fields (indigo, teal, rose) + a few orbs. Pure CSS transforms: cheap, paused under reduced motion. */
export function Background() {
  return (
    <div className="ep-bg" aria-hidden>
      <i className="f f1" />
      <i className="f f2" />
      <i className="f f3" />
      <i className="o o1" />
      <i className="o o2" />
      <i className="o o3" />
    </div>
  );
}
