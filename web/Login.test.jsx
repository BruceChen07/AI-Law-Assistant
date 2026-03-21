import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { vi } from "vitest"
import Login from "./Login"

vi.mock("./api", () => ({
  login: vi.fn(async () => ({ ok: true })),
  register: vi.fn(async () => ({ ok: true }))
}))

describe("Login", () => {
  it("calls onLogin after successful sign in", async () => {
    const onLogin = vi.fn()
    render(<Login onLogin={onLogin} />)

    fireEvent.change(screen.getByPlaceholderText("Username"), { target: { value: "demo" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "secret" } })
    fireEvent.click(screen.getByRole("button", { name: "Login" }))

    await waitFor(() => {
      expect(onLogin).toHaveBeenCalledTimes(1)
    })
  })
})
