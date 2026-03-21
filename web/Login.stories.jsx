import Login from "./Login"

const meta = {
  title: "Auth/Login",
  component: Login,
  args: {
    onLogin: () => {}
  }
}

export default meta

export const SignIn = {}

export const RegisterMode = {
  play: async ({ canvasElement }) => {
    const link = canvasElement.querySelector("a")
    if (link) link.click()
  }
}
