import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

function setup(requireText?: string) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      open
      title="Delete files?"
      body="This removes 3 files from disk."
      confirmLabel="Delete"
      destructive
      requireText={requireText}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  );
  return { onConfirm, onCancel, user: userEvent.setup() };
}

describe("ConfirmDialog", () => {
  it("renders title and body and confirms", async () => {
    const { onConfirm, user } = setup();
    expect(screen.getByRole("dialog", { name: "Delete files?" })).toBeInTheDocument();
    expect(screen.getByText("This removes 3 files from disk.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("keeps confirm disabled until the required text is typed exactly", async () => {
    const { onConfirm, user } = setup("DELETE");
    const confirm = screen.getByRole("button", { name: "Delete" });
    const input = screen.getByRole("textbox");
    expect(confirm).toBeDisabled();

    await user.type(input, "delete");
    expect(confirm).toBeDisabled();
    await user.clear(input);
    await user.type(input, "DELETE ");
    expect(confirm).toBeDisabled();
    await user.clear(input);
    await user.type(input, "DELETE");
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("cancels with the cancel button and Escape", async () => {
    const { onCancel, user } = setup();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("renders nothing when closed", () => {
    render(
      <ConfirmDialog open={false} title="T" body="B" confirmLabel="OK" onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
