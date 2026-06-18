FROM ruby:3-alpine
RUN apk add --update build-base curl
WORKDIR /app
COPY Gemfile .
RUN bundle install
COPY test_app.rb .
CMD ["ruby", "test_app.rb"]
EXPOSE 4567
